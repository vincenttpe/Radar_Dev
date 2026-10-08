"""知識圖譜 v1：從正本 graph.cypher 解析，並產生 metrics.json 與 thresholds.json。

graph.cypher 是人改的唯一來源；兩個 JSON 都是它的衍生檔，請勿手改：

    radar-kg-build           # 依 graph.cypher 重新產生 metrics.json、thresholds.json
    radar-kg-build --check   # 只檢查是否一致，不一致以非零結束

改完 graph.cypher 的順序：radar-kg-build → radar-kg-manifest（更新雜湊）→ pytest。
需要先 pip install -e .（見 pyproject.toml）。

解析 Cypher 的規則只在這個檔案裡寫一次，測試也呼叫這裡的函式，不再各自抄一份。
"""
from __future__ import annotations

import json
import re
import sys
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any, TypedDict

REPO = Path(__file__).resolve().parents[2]
VERSION = "v1"
CYPHER = f"kg/{VERSION}/graph.cypher"
METRICS_JSON = f"kg/{VERSION}/metrics.json"
THRESHOLDS_JSON = f"kg/{VERSION}/thresholds.json"

# ── 人訂的欄位 ────────────────────────────────────────────
# 單位與量測精度不在 graph.cypher 裡，由人依來源寫出的位數判斷（尚待審核人確認，
# 見 docs/05_會議待詢問清單.md E5）。resolution 是量測值的最小間距，None 代表連續量。
METRIC_CONFIG: dict[str, dict[str, Any]] = {
    "下肢肌力": {"unit": "次/30 秒", "resolution": 1, "resolution_note": "次數，整數"},
    "單腳站立": {"unit": "秒", "resolution": 1, "resolution_note": "秒數，來源只寫整數秒（假設，來源未明說精度）"},
    "來回行走": {"unit": "秒（2.44 公尺）", "resolution": 0.1, "resolution_note": "秒數，來源寫到小數一位（假設，來源未明說量測精度與取整方式）"},
    "心肺耐力": {"unit": "次/2 分鐘", "resolution": 1, "resolution_note": "次數，整數"},
    "步行速度": {"unit": "m/s", "resolution": None, "resolution_note": "連續量，來源以共用邊界寫成區間，未定義邊界歸屬"},
}

METRICS_HEADER = {
    "version": VERSION,
    "source": f"{CYPHER}（由 kg/source/graph.docx 抽出）",
    "note": "由 radar-kg-build 從 graph.cypher 產生，請勿手改；修改請改 graph.cypher 再執行 radar-kg-build",
}
THRESHOLDS_HEADER = {
    "version": VERSION,
    "source": "由 graph.cypher 的等級名稱逐字轉出；只是結構化，沒有修正任何門檻",
    "note": "min／max 為來源寫出的數字，'~' 與 '以上' 一律照字面視為含邊界（min_inclusive、max_inclusive 皆 true）。來源沒有定義邊界歸屬與量測精度；已知的缺口與重疊見 tests/test_thresholds.py 的 KNOWN_ISSUES，待審核人決定（docs/05_會議待詢問清單.md E5）。",
}


class Level(TypedDict):
    name: str
    score: int
    rank: str
    suggestion: str
    recommended_dose: str | None


class Metric(TypedDict):
    name: str
    levels: list[Level]


# ── 解析 graph.cypher ─────────────────────────────────────

_METRIC = re.compile(r'\((m\d+):Metric \{name: "([^"]*)"\}\)')
_CRITERIA = re.compile(r'CREATE \((c\d+_\d+):Criteria \{name: "([^"]*)", score: (\d+), rank: "([^"]*)"\}\)')
_SUGGESTION = re.compile(r'CREATE \((s\d+_\d+):Suggestion \{content: "((?:[^"\\]|\\.)*)"\}\)')
_LINK = re.compile(r"\((m\d+)\)-\[:has_level\]->\((c\d+_\d+)\)-\[:give_advice\]->\((s\d+_\d+)\)")
_DOSE_MARK = "【建議數值】"


def _links(cypher: str) -> list[tuple[str, str, int, str, str]]:
    """每個等級一筆：(指標名稱, 等級名稱, 分數, rank, 建議原文)。依 graph.cypher 出現順序。

    建議原文保留 Cypher 字串裡的跳脫（\\n 仍是兩個字元），給需要逐字比對的地方用。
    """
    metrics = dict(_METRIC.findall(cypher))
    criteria = {v: (name, int(score), rank) for v, name, score, rank in _CRITERIA.findall(cypher)}
    suggestions = dict(_SUGGESTION.findall(cypher))
    out = []
    for m, c, s in _LINK.findall(cypher):
        if m not in metrics or c not in criteria or s not in suggestions:
            raise ValueError(f"graph.cypher 的連結 ({m})-({c})-({s}) 找不到對應的節點定義")
        name, score, rank = criteria[c]
        out.append((metrics[m], name, score, rank, suggestions[s]))
    if not out:
        raise ValueError("graph.cypher 解析不到任何等級")
    return out


def parse_cypher(cypher: str) -> list[Metric]:
    """指標與等級；指標與等級都依 graph.cypher 出現順序。"""
    metrics: dict[str, Metric] = {}
    for metric, name, score, rank, raw in _links(cypher):
        text, _, dose = raw.replace("\\n", "\n").partition(_DOSE_MARK)
        level: Level = {
            "name": name, "score": score, "rank": rank,
            "suggestion": text.strip(), "recommended_dose": dose.strip() or None,
        }
        metrics.setdefault(metric, {"name": metric, "levels": []})["levels"].append(level)
    return list(metrics.values())


def raw_suggestions(cypher: str) -> dict[tuple[str, int], str]:
    """{(指標, 分數): Suggestion 原文}，用來和 patch_wording.cypher 逐字比對。"""
    return {(metric, score): raw for metric, _n, score, _r, raw in _links(cypher)}


def parse_patch(patch: str) -> list[tuple[str, int, str]]:
    """patch_wording.cypher 裡的每段 SET：(指標, 分數, 內容原文)。"""
    pattern = re.compile(
        r'MATCH \(:Metric \{name: "([^"]*)"\}\)-\[:has_level\]->\(:Criteria \{score: (\d+)\}\)'
        r'-\[:give_advice\]->\(s:Suggestion\)\s*SET s\.content = "((?:[^"\\]|\\.)*)";'
    )
    return [(m, int(score), content) for m, score, content in pattern.findall(patch)]


# ── 門檻 ─────────────────────────────────────────────────

_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*~\s*(\d+(?:\.\d+)?)")
_OPEN_ENDED = re.compile(r"(\d+(?:\.\d+)?)\s*(?:秒|次|m/s)?\s*以上")


def _number(text: str) -> int | float:
    value = float(text)
    return int(value) if value == int(value) else value


def parse_level_range(level_name: str) -> tuple[int | float, int | float | None]:
    """'起站30秒 14~17次' → (14, 17)；'起站30秒 18次以上' → (18, None)。"""
    if m := _RANGE.search(level_name):
        return _number(m.group(1)), _number(m.group(2))
    if m := _OPEN_ENDED.search(level_name):
        return _number(m.group(1)), None
    raise ValueError(f"無法從等級名稱解析門檻：{level_name!r}")


def build_thresholds(metrics: list[Metric]) -> dict[str, Any]:
    out = []
    for metric in metrics:
        if metric["name"] not in METRIC_CONFIG:
            raise ValueError(f'指標 {metric["name"]!r} 沒有單位與量測精度設定，請補到 kg_source.METRIC_CONFIG')
        levels = []
        for level in metric["levels"]:
            low, high = parse_level_range(level["name"])
            levels.append({
                "score": level["score"], "rank": level["rank"], "min": low, "max": high,
                "min_inclusive": True, "max_inclusive": high is not None, "source_text": level["name"],
            })
        out.append({"name": metric["name"], **METRIC_CONFIG[metric["name"]], "levels": levels})
    return {**THRESHOLDS_HEADER, "metrics": out}


def _overlaps(low: dict[str, Any], high: dict[str, Any]) -> bool:
    """較低等級的上界是否和較高等級的下界重疊（相等且兩邊都含也算重疊）。"""
    edge_low, edge_high = Decimal(str(low["max"])), Decimal(str(high["min"]))
    return edge_low > edge_high or (edge_low == edge_high and low["max_inclusive"] and high["min_inclusive"])


def _has_gap(low: dict[str, Any], high: dict[str, Any], resolution: Decimal | None) -> bool:
    """兩級之間是否有量得到、卻不屬於任何一級的值。resolution 為 None 代表連續量。"""
    edge_low, edge_high = Decimal(str(low["max"])), Decimal(str(high["min"]))
    if resolution is None:  # 連續量：邊界相等且至少一邊含才相接
        return not (edge_low == edge_high and (low["max_inclusive"] or high["min_inclusive"]))
    # 離散量：低等級之後第一個沒被涵蓋的值，必須被高等級涵蓋
    first_uncovered = edge_low + resolution if low["max_inclusive"] else edge_low
    first_covered_by_high = edge_high if high["min_inclusive"] else edge_high + resolution
    return first_uncovered < first_covered_by_high


def find_threshold_issues(metric: dict[str, Any]) -> set[tuple[Any, ...]]:
    """依指標的 resolution 檢查相鄰等級有沒有缺口或重疊，回傳 {(指標, 低分, 高分, 'gap'|'overlap')}。

    把等級依 min 排序，逐對比較「較低那級的上界」與「較高那級的下界」。
    """
    resolution = None if metric["resolution"] is None else Decimal(str(metric["resolution"]))
    levels = sorted(metric["levels"], key=lambda lv: Decimal(str(lv["min"])))
    issues = set()
    for low, high in pairwise(levels):
        if low["max"] is None:
            raise ValueError(f'{metric["name"]} 分數 {low["score"]} 是開放上界，卻不是最高那一級')
        key = (metric["name"], low["score"], high["score"])
        if _overlaps(low, high):
            issues.add((*key, "overlap"))
        elif _has_gap(low, high, resolution):
            issues.add((*key, "gap"))
    return issues


# ── 產生檔案 ──────────────────────────────────────────────

def _dump(obj: dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def build_all(repo: Path = REPO) -> dict[str, str]:
    """{repo 相對路徑: 應有的檔案內容}。"""
    metrics = parse_cypher((repo / CYPHER).read_text(encoding="utf-8"))
    return {
        METRICS_JSON: _dump({**METRICS_HEADER, "metrics": metrics}),
        THRESHOLDS_JSON: _dump(build_thresholds(metrics)),
    }


def main(argv: list[str] | None = None, repo: Path = REPO) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        outputs = build_all(repo)
    except ValueError as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 2
    stale = [p for p, text in outputs.items() if not (repo / p).exists() or (repo / p).read_text(encoding="utf-8") != text]

    if "--check" in argv:
        if stale:
            print("以下檔案與 graph.cypher 不一致，請執行 radar-kg-build：\n  " + "\n  ".join(stale))
            return 1
        print("metrics.json、thresholds.json 與 graph.cypher 一致")
        return 0

    for path in stale:
        (repo / path).write_text(outputs[path], encoding="utf-8")
    print("已更新：" + "、".join(stale) if stale else "沒有變動")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
