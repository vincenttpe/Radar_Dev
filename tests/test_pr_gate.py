"""PR 單向門檢查（radar_agent.pr_gate）：勾選與變更檔案的對應，以及與 PR 範本、REVIEW.md 的一致性。"""
import re

import pytest

from radar_agent import pr_gate
from radar_agent.pr_gate import DOORS, NONE_LABEL, check, required_doors


@pytest.fixture(scope="module")
def template(repo) -> str:
    return (repo / ".github" / "pull_request_template.md").read_text(encoding="utf-8")


def _body(template: str, checked: list[str], reversibility: str = "") -> str:
    """用真的 PR 範本勾選指定項目、填寫可逆性。"""
    lines = []
    for line in template.splitlines():
        out = line
        m = re.match(r"^- \[ \] (.+)$", line)
        if m and any(m.group(1).startswith(label) for label in checked):
            out = f"- [x] {m.group(1)}"
        if line.startswith(pr_gate.REVERSIBILITY) and reversibility:
            out = f"{line}{reversibility}"
        lines.append(out)
    return "\n".join(lines)


# ── 範本、REVIEW.md 與 DOORS 一致 ─────────────────────────

def test_template_checkboxes_are_exactly_the_doors_plus_none(template):
    section = pr_gate._section(template)
    assert section is not None
    labels = [m.group(1) for m in re.finditer(r"^- \[ \] (.+)$", section, re.MULTILINE)]
    assert [pr_gate._label(text) for text in labels] == [*DOORS, NONE_LABEL]


def test_review_md_door_table_lists_every_door(repo):
    review = (repo / "REVIEW.md").read_text(encoding="utf-8")
    table_doors = re.findall(r"^\| (\S+) \| ", review.split("## 2.")[1].split("## 3.")[0], re.MULTILINE)
    assert [d for d in table_doors if d not in {"單向門", "---"}] == list(DOORS)


def test_every_door_path_exists_in_the_repo(repo):
    """路徑前綴打錯字，CI 就永遠擋不到；每個前綴都要對到 repo 裡真的存在的東西。"""
    for door, prefixes in DOORS.items():
        for prefix in prefixes:
            assert list(repo.glob(prefix + "*")), (door, prefix)


def test_unfilled_template_is_rejected(template):
    assert check(template, ["README.md"]) == ["單向門檢查沒有勾選任何項目；沒有碰到單向門請勾「以上皆無」"]


# ── 勾選與變更檔案 ─────────────────────────────────────

def test_docs_only_change_passes_with_none(template):
    assert check(_body(template, [NONE_LABEL]), ["docs/02_目標架構.md", "README.md"]) == []


@pytest.mark.parametrize("path,door", [
    ("kg/v1/graph.cypher", "知識圖譜內容"),
    ("kg/v1.1/metrics.json", "知識圖譜內容"),
    ("kg/releases.json", "知識圖譜內容"),
    ("src/radar_agent/kg_manifest.py", "知識圖譜內容"),
    ("src/radar_agent/kg_source.py", "數字與等級的判定"),
    ("src/radar_agent/kg_rules.py", "數字與等級的判定"),
    ("src/radar_agent/weekly_report.py", "週報統計口徑"),
    ("data/samples_fall/2026-06-03.json", "個人資料的流向"),
    ("schemas/event.v0.draft.json", "事件格式"),
    ("src/radar_agent/event_validation.py", "事件格式"),
    (".github/workflows/tests.yml", "檢查與審查機制"),
    ("REVIEW.md", "檢查與審查機制"),
    ("src/radar_agent/pr_gate.py", "檢查與審查機制"),
])
def test_touching_a_door_without_checking_it_is_rejected(template, path, door):
    errors = check(_body(template, [NONE_LABEL]), [path])
    assert f"變更碰到單向門「{door}」但沒有勾選：{path}" in errors


def test_checking_the_touched_door_with_reversibility_passes(template):
    body = _body(template, ["週報統計口徑"], reversibility="只改排版；revert 後重新產生本週週報即可")
    assert check(body, ["src/radar_agent/weekly_report.py"]) == []


def test_door_checked_without_reversibility_is_rejected(template):
    assert check(_body(template, ["事件格式"]), ["schemas/event.v0.draft.json"]) == [
        "勾選了單向門，但「可逆性與回復方式」沒有填寫",
    ]


def test_reversibility_written_on_the_following_line_counts(template):
    line = next(s for s in template.splitlines() if s.startswith(pr_gate.REVERSIBILITY))
    body = _body(template, ["事件格式"]).replace(line, f"{line}\n\n草案階段，雙方尚未照此格式送資料", 1)
    assert check(body, ["schemas/event.v0.draft.json"]) == []


def test_none_together_with_a_door_is_rejected(template):
    body = _body(template, [NONE_LABEL, "知識圖譜內容"], reversibility="可還原")
    assert "「以上皆無」不能和單向門同時勾選：知識圖譜內容" in check(body, ["kg/v1/graph.cypher"])


def test_checking_a_door_the_diff_does_not_touch_is_allowed(template):
    """沒有對應路徑的單向門（例如給讀者的輸出）只能靠作者主動勾，不能因為沒碰到路徑就擋。"""
    assert check(_body(template, ["給讀者的輸出與用語"], reversibility="可還原"), ["docs/x.md"]) == []


def test_missing_section_is_rejected():
    assert check("隨手寫的說明", ["README.md"]) == ["PR 說明缺少「單向門檢查」一節，請使用 .github/pull_request_template.md"]


def test_checkbox_inside_html_comment_does_not_count(template):
    body = _body(template, []).replace("## 單向門檢查", "## 單向門檢查\n<!-- - [x] 以上皆無 -->", 1)
    assert check(body, []) == ["單向門檢查沒有勾選任何項目；沒有碰到單向門請勾「以上皆無」"]


def test_crlf_line_endings_from_github_are_handled(template):
    body = _body(template, [NONE_LABEL]).replace("\n", "\r\n")
    assert check(body, ["README.md"]) == []


def test_unknown_checked_item_does_not_count_as_a_door_or_none():
    body = "## 單向門檢查\n\n- [x] 其他自己加的項目\n"
    assert check(body, []) == ["單向門檢查沒有勾選任何項目；沒有碰到單向門請勾「以上皆無」"]


def test_door_checked_but_reversibility_line_deleted_is_rejected():
    body = "## 單向門檢查\n\n- [x] 事件格式（`schemas/`）\n\n## 影響範圍\n\n可還原\n"
    assert check(body, ["schemas/event.v0.draft.json"]) == ["勾選了單向門，但「可逆性與回復方式」沒有填寫"]


def test_required_doors_groups_files_by_door():
    assert required_doors(["kg/v1/graph.cypher", "README.md", "kg/releases.json"]) == {
        "知識圖譜內容": ["kg/v1/graph.cypher", "kg/releases.json"],
    }


# ── 命令列 ──────────────────────────────────────────────

def test_main_passes_and_names_doors_needing_review(template, tmp_path, capsys):
    body, changed = tmp_path / "body.md", tmp_path / "changed.txt"
    body.write_text(_body(template, ["週報統計口徑"], reversibility="可還原"), encoding="utf-8")
    changed.write_text("src/radar_agent/weekly_report.py\n\nREADME.md\n", encoding="utf-8")
    assert pr_gate.main(["--body", str(body), "--changed", str(changed)]) == 0
    assert capsys.readouterr().out == "單向門檢查通過；需要人工深入審查：週報統計口徑\n"


def test_main_passes_without_doors(template, tmp_path, capsys):
    body, changed = tmp_path / "body.md", tmp_path / "changed.txt"
    body.write_text(_body(template, [NONE_LABEL]), encoding="utf-8")
    changed.write_text("README.md\n", encoding="utf-8")
    assert pr_gate.main(["--body", str(body), "--changed", str(changed)]) == 0
    assert capsys.readouterr().out == "單向門檢查通過；沒有碰到單向門\n"


def test_main_fails_with_reasons_on_stderr(template, tmp_path, capsys):
    body, changed = tmp_path / "body.md", tmp_path / "changed.txt"
    body.write_text(_body(template, [NONE_LABEL]), encoding="utf-8")
    changed.write_text("kg/v1/graph.cypher\n", encoding="utf-8")
    assert pr_gate.main(["--body", str(body), "--changed", str(changed)]) == 1
    err = capsys.readouterr().err
    assert "單向門檢查未通過" in err
    assert "變更碰到單向門「知識圖譜內容」但沒有勾選：kg/v1/graph.cypher" in err
