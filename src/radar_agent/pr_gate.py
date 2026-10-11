"""PR 說明的檢查：依變更的檔案，確認 PR 說明勾了對應的單向門與驗證證據。

單向門的定義見 REVIEW.md 第 2 節；PR 範本（.github/pull_request_template.md）讓作者勾選。
只靠作者自律，改了 kg/ 卻勾「以上皆無」也看不出來，所以由 CI（.github/workflows/pr-gate.yml）
依變更路徑檢查：

- 單向門：至少勾一項；「以上皆無」不能和單向門同時勾；變更碰到某個單向門的路徑，就必須勾那一項；
  勾了任何單向門，「可逆性與回復方式」必須填寫。
- 驗證證據：變更碰到知識圖譜或指令列工具時，對應的驗證項目必須勾選。ruff、mypy、pytest
  三項 CI 本來就會跑，不另外要求勾選。

路徑對應只寫在 DOORS 與 EVIDENCE；REVIEW.md 的表格與 PR 範本的勾選項由 tests/test_pr_gate.py
檢查是否一致。路徑對應只能擋下已知的檔案；沒有對應路徑的單向門（例如給讀者的輸出）仍靠作者勾選與人工審查。

執行：
    python -m radar_agent.pr_gate --body PR說明.md --changed 變更檔案清單.txt
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# 單向門名稱 → 路徑前綴（repo 相對路徑）。名稱要與 PR 範本勾選項的開頭、REVIEW.md 表格一致。
DOORS: dict[str, tuple[str, ...]] = {
    "知識圖譜內容": (
        "kg/v", "kg/releases.json", "kg/source/", "kg/dumps/",
        "src/radar_agent/kg_manifest.py",  # 雜湊比對與已核准版本的鎖定
    ),
    "數字與等級的判定": (
        "src/radar_agent/kg_rules.py",
        "src/radar_agent/kg_source.py",  # 解析門檻、產生 thresholds.json、METRIC_CONFIG
    ),
    "週報統計口徑": ("src/radar_agent/weekly_report.py",),
    "給讀者的輸出與用語": (),  # Profile 範本與輸出檢查尚未實作，有了再補路徑
    "個人資料的流向": ("data/",),
    "事件格式": ("schemas/", "src/radar_agent/event_validation.py"),
    "檢查與審查機制": (
        ".github/", "REVIEW.md",
        "src/radar_agent/pr_gate.py",
    ),
}
NONE_LABEL = "以上皆無"
DOORS_SECTION = "## 單向門檢查"
REVERSIBILITY_HEADING = "**可逆性與回復方式**"

# 驗證證據項目 → 路徑前綴。名稱要與 PR 範本「驗證證據」勾選項的開頭一致。
EVIDENCE: dict[str, tuple[str, ...]] = {
    "改到知識圖譜": (
        "kg/v", "kg/releases.json", "kg/source/",
        "src/radar_agent/kg_source.py", "src/radar_agent/kg_manifest.py",
    ),
    "改到指令或報告": (  # pyproject.toml 的 [project.scripts] 指向的模組
        "src/radar_agent/weekly_report.py", "src/radar_agent/kg_rules.py",
        "src/radar_agent/kg_source.py", "src/radar_agent/kg_manifest.py",
    ),
}
EVIDENCE_SECTION = "## 驗證證據"

_CHECKBOX = re.compile(r"^\s*[-*]\s*\[([ xX])\]\s*(.+?)\s*$")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_HINT = re.compile(r"^（[^）]*）")


def _section(body: str, heading: str) -> str | None:
    """heading 到下一個「## 」之間的內容（已去掉 HTML 註解）；沒有這一節回傳 None。"""
    lines = _COMMENT.sub("", body).splitlines()
    for i, line in enumerate(lines):
        if line.strip() == heading:
            rest = lines[i + 1:]
            end = next((j for j, s in enumerate(rest) if s.startswith("## ")), len(rest))
            return "\n".join(rest[:end])
    return None


def _label(text: str, labels: list[str]) -> str | None:
    return next((label for label in labels if text.startswith(label)), None)


def checked_labels(section: str, labels: list[str]) -> set[str]:
    """section 裡勾選的項目中，開頭符合 labels 的名稱；認不出的勾選項不算。"""
    found = set()
    for line in section.splitlines():
        m = _CHECKBOX.match(line)
        if m and m.group(1) in "xX":
            label = _label(m.group(2), labels)
            if label:
                found.add(label)
    return found


def reversibility(section: str) -> str:
    """「可逆性與回復方式」之後填寫的文字（同一行冒號之後加上其後各行）。

    範本在標題後附了「（有勾選單向門時必填）」這類提示，提示不算填寫。
    """
    _, found, after = section.partition(REVERSIBILITY_HEADING)
    if not found:
        return ""
    return _HINT.sub("", after.lstrip()).lstrip("：:").strip()


def _hits(changed: list[str], mapping: dict[str, tuple[str, ...]]) -> dict[str, list[str]]:
    """{項目: 碰到它的變更檔案}，只列有碰到的。"""
    hits: dict[str, list[str]] = {}
    for path in changed:
        for name, prefixes in mapping.items():
            if any(path.startswith(p) for p in prefixes):
                hits.setdefault(name, []).append(path)
    return hits


def required_doors(changed: list[str]) -> dict[str, list[str]]:
    """{單向門: 碰到它的變更檔案}，只列有碰到的。"""
    return _hits(changed, DOORS)


def required_evidence(changed: list[str]) -> dict[str, list[str]]:
    """{驗證證據項目: 需要它的變更檔案}，只列有碰到的。"""
    return _hits(changed, EVIDENCE)


def _door_errors(section: str, doors_hit: dict[str, list[str]]) -> list[str]:
    checked = checked_labels(section, [NONE_LABEL, *DOORS])
    doors = checked - {NONE_LABEL}
    errors = []
    if not checked:
        errors.append("單向門檢查沒有勾選任何項目；沒有碰到單向門請勾「以上皆無」")
    if NONE_LABEL in checked and doors:
        errors.append(f"「以上皆無」不能和單向門同時勾選：{'、'.join(sorted(doors))}")
    for door, paths in doors_hit.items():
        if door not in checked:
            errors.append(f"變更碰到單向門「{door}」但沒有勾選：{'、'.join(paths)}")
    if doors and not reversibility(section):
        errors.append("勾選了單向門，但「可逆性與回復方式」沒有填寫")
    return errors


def _evidence_errors(body: str, evidence_hit: dict[str, list[str]]) -> list[str]:
    if not evidence_hit:
        return []
    section = _section(body, EVIDENCE_SECTION)
    if section is None:
        return [f"PR 說明缺少「{EVIDENCE_SECTION.removeprefix('## ')}」一節，請使用 .github/pull_request_template.md"]
    checked = checked_labels(section, list(EVIDENCE))
    return [
        f"變更需要驗證證據「{item}」但沒有勾選：{'、'.join(paths)}"
        for item, paths in evidence_hit.items() if item not in checked
    ]


def _errors(body: str, doors_hit: dict[str, list[str]], evidence_hit: dict[str, list[str]]) -> list[str]:
    section = _section(body, DOORS_SECTION)
    if section is None:
        return [f"PR 說明缺少「{DOORS_SECTION.removeprefix('## ')}」一節，請使用 .github/pull_request_template.md"]
    return _door_errors(section, doors_hit) + _evidence_errors(body, evidence_hit)


def quoted_paths(changed: list[str]) -> list[str]:
    """被 git 加上引號轉義的路徑（例如 "kg/v1/\\351…"）。

    這種路徑對不上任何單向門的前綴，會讓檢查默默通過，所以一律擋下。git diff 預設會把中文檔名
    轉義，要加 -c core.quotePath=false；含 tab、引號等特殊字元的路徑即使加了也會被轉義。
    """
    return [p for p in changed if p.startswith('"')]


def check(body: str, changed: list[str]) -> list[str]:
    """回傳錯誤訊息清單；空清單代表通過。"""
    quoted = quoted_paths(changed)
    if quoted:
        return [(
            f"變更檔案清單裡有被 git 轉義的路徑，無法比對單向門：{'、'.join(quoted)}；"
            "請用 git -c core.quotePath=false diff --name-only 產生清單（檔名不能含 tab、引號等特殊字元）"
        )]
    return _errors(body, required_doors(changed), required_evidence(changed))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="檢查 PR 說明的單向門與驗證證據勾選是否與變更的檔案一致")
    ap.add_argument("--body", required=True, help="PR 說明的檔案")
    ap.add_argument("--changed", required=True, help="變更檔案清單，一行一個 repo 相對路徑")
    args = ap.parse_args(argv)

    body = Path(args.body).read_text(encoding="utf-8")
    changed = [s.strip() for s in Path(args.changed).read_text(encoding="utf-8").splitlines() if s.strip()]
    doors_hit = required_doors(changed)
    errors = check(body, changed)
    if errors:
        print("PR 說明檢查未通過（定義見 REVIEW.md 第 2 節）：", file=sys.stderr)
        for e in errors:
            print(f"- {e}", file=sys.stderr)
        return 1
    print("單向門檢查通過" + (f"；需要人工深入審查：{'、'.join(doors_hit)}" if doors_hit else "；沒有碰到單向門"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
