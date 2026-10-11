"""規則引擎：一個量測值 → 知識圖譜的等級、分數與建議。

原則 2：等級由規則決定，模型只改寫。這裡就是那條「規則」，不用 LLM、不猜：

- 只讀 kg/<版本>/thresholds.json（門檻）與 metrics.json（建議），兩者都由 radar-kg-build 從 graph.cypher 產生。
- 讀之前比對 kg/releases.json 的雜湊；檔案和登記的不同，就拒絕評估（不能把內容不同的檔案當成同一版）。
- 每筆結果都記下用了哪一版知識圖譜，以及那一版是否已核准。未核准時結果照給，但標明「僅供測試」。
- 數值落在門檻的缺口（不屬於任何等級）或重疊（同時屬於兩級），一律「無法評估」並說明原因，
  不取整、不挑一級：邊界怎麼歸屬是醫療判斷，待審核人決定（會議清單 E5）。
- 知識圖譜沒有的指標回傳 no_rule，不輸出等級；呼叫端應把它送進審核佇列。

數值從哪裡來（雷達自動量測或人工施測輸入）尚未決定（會議清單 E7），這裡只負責「數值 → 等級」。

    radar-kg-level 來回行走 5.4          # 印出等級與建議
    radar-kg-level 來回行走 5.4 --json
    radar-kg-level --list                # 列出指標、單位與等級門檻

結束碼：0＝得到等級；1＝無法評估或沒有規則；2＝輸入或知識圖譜檔案有誤。需要先 pip install -e .。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal, TypedDict

from radar_agent.kg_manifest import KgIntegrityError as KgIntegrityError  # 呼叫者與測試沿用 kg_rules.KgIntegrityError
from radar_agent.kg_manifest import verify_release

REPO = Path(__file__).resolve().parents[2]
DEFAULT_VERSION = "v1"

Status = Literal["assessed", "unassessable", "no_rule"]


class KgInfo(TypedDict):
    version: str
    approval: str         # releases.json 的 approval.status（pending／approved…）
    approved: bool


class Candidate(TypedDict):
    score: int
    rank: str
    source_text: str


class Assessment(TypedDict):
    metric: str
    value: str            # 原樣保留輸入的十進位寫法，不轉成浮點數
    unit: str | None
    status: Status
    score: int | None
    rank: str | None
    source_text: str | None      # 等級在知識圖譜裡的原文，例如「來回行走 5.4秒以下」
    suggestion: str | None
    recommended_dose: str | None
    reason: str | None           # status 不是 assessed 時的原因
    candidates: list[Candidate]  # 重疊時同時符合的等級；其他情況為空
    kg: KgInfo


@dataclass(frozen=True)
class KnowledgeGraph:
    info: KgInfo
    thresholds: dict[str, dict[str, Any]]                 # 指標名稱 → thresholds.json 的那一項
    suggestions: dict[tuple[str, int], dict[str, Any]]    # (指標, 分數) → metrics.json 的等級


# ── 讀入 ─────────────────────────────────────────────────

def load(version: str = DEFAULT_VERSION, repo: Path = REPO) -> KnowledgeGraph:
    """讀入某一版知識圖譜；清單或檔案不存在、雜湊與 releases.json 不符時丟 KgIntegrityError。"""
    release = verify_release(version, ("thresholds.json", "metrics.json"), repo)
    thresholds = json.loads((repo / "kg" / version / "thresholds.json").read_text(encoding="utf-8"))
    metrics = json.loads((repo / "kg" / version / "metrics.json").read_text(encoding="utf-8"))
    return KnowledgeGraph(
        info={"version": release.version, "approval": release.approval, "approved": release.approved},
        thresholds={m["name"]: m for m in thresholds["metrics"]},
        suggestions={(m["name"], lv["score"]): lv for m in metrics["metrics"] for lv in m["levels"]},
    )


# ── 評估 ─────────────────────────────────────────────────

def _decimal(value: object) -> Decimal | None:
    """有限的十進位數；bool、NaN、無限大、無法解析都回 None。"""
    if isinstance(value, bool):
        return None
    try:
        d = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def _contains(level: dict[str, Any], value: Decimal) -> bool:
    low = Decimal(str(level["min"]))
    if value < low or (value == low and not level["min_inclusive"]):
        return False
    if level["max"] is None:
        return True
    high = Decimal(str(level["max"]))
    return value < high or (value == high and level["max_inclusive"])


def _lowest(metric: dict[str, Any]) -> Decimal:
    return min(Decimal(str(lv["min"])) for lv in metric["levels"])


def _is_multiple(value: Decimal, step: Decimal) -> bool:
    """value 是否為 step 的整數倍。用分數做整數運算，不受 Decimal 精度限制（1e100 也算得出來）。"""
    p, q = value.as_integer_ratio()
    r, s = step.as_integer_ratio()
    return (p * s) % (q * r) == 0


def _precision_reason(metric: dict[str, Any], value: Decimal) -> str | None:
    """數值比來源寫出的精度更細時的說明；不論是否落在某個等級的區間內，都不評分。

    低於所有等級下限的數值交給 _gap_reason，維持「低於下限」優先回報。
    """
    resolution = metric["resolution"]
    if resolution is None or value < _lowest(metric) or _is_multiple(value, Decimal(str(resolution))):
        return None
    return f"數值 {value} 比來源寫出的精度（{resolution}）更細；來源沒有定義取整方式，不由程式代決定（會議清單 E5）"


def _gap_reason(metric: dict[str, Any], value: Decimal) -> str:
    """數值不屬於任何等級時的說明：低於最低門檻，或落在兩級之間（精度已先檢查過）。"""
    lowest = _lowest(metric)
    if value < lowest:
        return f"數值 {value} 低於所有等級的下限 {lowest}"
    return f"數值 {value} 不屬於任何等級（門檻缺口，待審核人決定，會議清單 E5）"


def _result(metric: str, value: str, kg: KnowledgeGraph, status: Status, **fields: Any) -> Assessment:
    unit = kg.thresholds[metric]["unit"] if metric in kg.thresholds else None
    base: Assessment = {
        "metric": metric, "value": value, "unit": unit, "status": status,
        "score": None, "rank": None, "source_text": None, "suggestion": None, "recommended_dose": None,
        "reason": None, "candidates": [], "kg": kg.info,
    }
    return {**base, **fields}  # type: ignore[typeddict-item]


def assess(kg: KnowledgeGraph, metric: str, value: object) -> Assessment:
    """一個指標的一個量測值 → 等級。只有恰好落在一個等級時才是 assessed。"""
    text = str(value).strip()
    if metric not in kg.thresholds:
        return _result(metric, text, kg, "no_rule", reason=f"知識圖譜 {kg.info['version']} 沒有「{metric}」這個指標，不輸出等級；請送審核佇列")
    number = _decimal(value)
    if number is None:
        return _result(metric, text, kg, "unassessable", reason=f"數值 {text!r} 不是有效的數字")

    spec = kg.thresholds[metric]
    precision = _precision_reason(spec, number)
    if precision is not None:
        return _result(metric, text, kg, "unassessable", reason=precision)
    matches = [lv for lv in spec["levels"] if _contains(lv, number)]
    if not matches:
        return _result(metric, text, kg, "unassessable", reason=_gap_reason(spec, number))
    if len(matches) > 1:
        candidates: list[Candidate] = [
            {"score": lv["score"], "rank": lv["rank"], "source_text": lv["source_text"]}
            for lv in sorted(matches, key=lambda lv: lv["score"])
        ]
        names = "、".join(f'{c["score"]} 分「{c["rank"]}」' for c in candidates)
        reason = f"數值 {number} 同時屬於 {names}（門檻重疊，邊界歸屬待審核人決定，會議清單 E5）"
        return _result(metric, text, kg, "unassessable", reason=reason, candidates=candidates)

    level = matches[0]
    advice = kg.suggestions[(metric, level["score"])]
    return _result(
        metric, text, kg, "assessed",
        score=level["score"], rank=level["rank"], source_text=level["source_text"],
        suggestion=advice["suggestion"], recommended_dose=advice["recommended_dose"],
    )


# ── 輸出 ─────────────────────────────────────────────────

def _kg_line(info: KgInfo) -> str:
    state = "已核准" if info["approved"] else f'未核准（{info["approval"]}），結果僅供測試'
    return f'知識圖譜 {info["version"]}：{state}'


def render_text(a: Assessment) -> str:
    unit = f' {a["unit"]}' if a["unit"] else ""
    head = f'{a["metric"]} {a["value"]}{unit}'
    if a["status"] == "assessed":
        lines = [
            f'{head} → {a["score"]} 分「{a["rank"]}」（{a["source_text"]}）',
            f'建議：{a["suggestion"]}',
        ]
        if a["recommended_dose"]:
            lines.append(f'建議數值：{a["recommended_dose"]}')
    else:
        label = "沒有規則" if a["status"] == "no_rule" else "無法評估"
        lines = [f'{head} → {label}：{a["reason"]}']
    return "\n".join([*lines, _kg_line(a["kg"])]) + "\n"


def render_list(kg: KnowledgeGraph) -> str:
    lines = [_kg_line(kg.info)]
    for spec in kg.thresholds.values():
        lines.append(f'{spec["name"]}（{spec["unit"]}）')
        lines += [f'  {lv["score"]} 分「{lv["rank"]}」：{lv["source_text"]}' for lv in spec["levels"]]
    return "\n".join(lines) + "\n"


# ── 命令列 ───────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="量測值 → 知識圖譜等級與建議（規則，不用模型）")
    ap.add_argument("metric", nargs="?", help="指標名稱，例如 來回行走")
    ap.add_argument("value", nargs="?", help="量測值，例如 5.4")
    ap.add_argument("--version", default=DEFAULT_VERSION, help=f"知識圖譜版本（預設 {DEFAULT_VERSION}）")
    ap.add_argument("--list", action="store_true", help="列出指標與等級門檻")
    ap.add_argument("--json", action="store_true", help="輸出 JSON")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    try:
        kg = load(args.version, REPO)
    except KgIntegrityError as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 2
    if args.list:
        sys.stdout.write(render_list(kg))
        return 0
    if args.metric is None or args.value is None:
        ap.print_usage(sys.stderr)
        print("需要指標名稱與量測值，或使用 --list", file=sys.stderr)
        return 2

    result = assess(kg, args.metric, args.value)
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n" if args.json else render_text(result))
    return 0 if result["status"] == "assessed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
