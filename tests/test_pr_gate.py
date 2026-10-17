"""PR 單向門檢查（radar_agent.pr_gate）：勾選與變更檔案的對應，以及與 PR 範本、REVIEW.md 的一致性。"""
import re

import pytest

from radar_agent import pr_gate
from radar_agent.pr_gate import DOORS, EVIDENCE, NONE_LABEL, check, required_doors, required_evidence


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
        if line.startswith(pr_gate.REVERSIBILITY_HEADING) and reversibility:
            out = f"{line}{reversibility}"
        lines.append(out)
    return "\n".join(lines)


# ── 範本、REVIEW.md 與 DOORS 一致 ─────────────────────────

def test_template_checkboxes_are_exactly_the_doors_plus_none(template):
    section = pr_gate._section(template, pr_gate.DOORS_SECTION)
    assert section is not None
    labels = [m.group(1) for m in re.finditer(r"^- \[ \] (.+)$", section, re.MULTILINE)]
    assert [pr_gate._label(text, [*DOORS, NONE_LABEL]) for text in labels] == [*DOORS, NONE_LABEL]


def test_template_has_a_checkbox_for_every_evidence_item(template):
    section = pr_gate._section(template, pr_gate.EVIDENCE_SECTION)
    assert section is not None
    labels = [m.group(1) for m in re.finditer(r"^- \[ \] (.+)$", section, re.MULTILINE)]
    assert [label for label in EVIDENCE if any(text.startswith(label) for text in labels)] == list(EVIDENCE)


def test_evidence_items_cover_every_console_script_module(repo):
    """指令列工具改了就要附實際執行的輸出；新增指令時 EVIDENCE 要跟著補。"""
    import tomllib

    scripts = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    modules = {"src/" + target.partition(":")[0].replace(".", "/") + ".py" for target in scripts.values()}
    assert modules == set(EVIDENCE["改到指令或報告"])


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
    ("src/radar_agent/caregiver.py", "給讀者的輸出與用語"),
    ("src/radar_agent/ask.py", "給讀者的輸出與用語"),
    ("src/radar_agent/care_record.py", "給讀者的輸出與用語"),
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
    body = _body(template, ["數字與等級的判定", "改到指令或報告"], reversibility="只改原因的說明文字；revert 即可")
    assert check(body, ["src/radar_agent/kg_rules.py"]) == []


def test_door_checked_without_reversibility_is_rejected(template):
    assert check(_body(template, ["事件格式"]), ["schemas/event.v0.draft.json"]) == [
        "勾選了單向門，但「可逆性與回復方式」沒有填寫",
    ]


def test_reversibility_written_on_the_following_line_counts(template):
    line = next(s for s in template.splitlines() if s.startswith(pr_gate.REVERSIBILITY_HEADING))
    body = _body(template, ["事件格式"]).replace(line, f"{line}\n\n草案階段，雙方尚未照此格式送資料", 1)
    assert check(body, ["schemas/event.v0.draft.json"]) == []


def test_none_together_with_a_door_is_rejected(template):
    body = _body(template, [NONE_LABEL, "知識圖譜內容"], reversibility="可還原")
    assert "「以上皆無」不能和單向門同時勾選：知識圖譜內容" in check(body, ["kg/v1/graph.cypher"])


def test_checking_a_door_the_diff_does_not_touch_is_allowed(template):
    """變更沒碰到某個單向門的路徑，作者仍可主動勾（例如尚未列進 DOORS 的新報告），不能因此擋下。"""
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


# ── 驗證證據 ─────────────────────────────────────────

@pytest.mark.parametrize("path,item", [
    ("kg/v1/graph.cypher", "改到知識圖譜"),
    ("kg/releases.json", "改到知識圖譜"),
    ("src/radar_agent/kg_source.py", "改到知識圖譜"),
    ("src/radar_agent/kg_rules.py", "改到指令或報告"),
])
def test_touching_kg_or_a_command_without_evidence_is_rejected(template, path, item):
    body = _body(template, list(DOORS), reversibility="可還原")
    assert f"變更需要驗證證據「{item}」但沒有勾選：{path}" in check(body, [path])


def test_kg_change_with_both_evidence_items_passes(template):
    body = _body(template, ["知識圖譜內容", "數字與等級的判定", "改到知識圖譜", "改到指令或報告"], reversibility="可還原")
    assert check(body, ["kg/v1/graph.cypher", "src/radar_agent/kg_source.py"]) == []


def test_evidence_is_not_required_for_docs_only_change(template):
    assert required_evidence(["docs/02_目標架構.md", "src/radar_agent/event_validation.py"]) == {}


def test_missing_evidence_section_is_rejected_when_evidence_is_needed():
    body = "## 單向門檢查\n\n- [x] 數字與等級的判定\n\n**可逆性與回復方式**：可還原\n"
    assert check(body, ["src/radar_agent/kg_rules.py"]) == [
        "PR 說明缺少「驗證證據」一節，請使用 .github/pull_request_template.md",
    ]


def test_required_doors_groups_files_by_door():
    assert required_doors(["kg/v1/graph.cypher", "README.md", "kg/releases.json"]) == {
        "知識圖譜內容": ["kg/v1/graph.cypher", "kg/releases.json"],
    }


# ── 命令列 ──────────────────────────────────────────────

def test_main_passes_and_names_doors_needing_review(template, tmp_path, capsys):
    body, changed = tmp_path / "body.md", tmp_path / "changed.txt"
    body.write_text(_body(template, ["數字與等級的判定", "改到指令或報告"], reversibility="可還原"), encoding="utf-8")
    changed.write_text("src/radar_agent/kg_rules.py\n\nREADME.md\n", encoding="utf-8")
    assert pr_gate.main(["--body", str(body), "--changed", str(changed)]) == 0
    assert capsys.readouterr().out == "單向門檢查通過；需要人工深入審查：數字與等級的判定\n"


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
    assert "PR 說明檢查未通過" in err
    assert "變更碰到單向門「知識圖譜內容」但沒有勾選：kg/v1/graph.cypher" in err


# ── 中文檔名與被 git 轉義的路徑 ───────────────────────────

def test_non_ascii_path_under_a_door_requires_the_door(template):
    assert "變更碰到單向門「知識圖譜內容」但沒有勾選：kg/v1/門檻.json" in check(_body(template, [NONE_LABEL]), ["kg/v1/門檻.json"])


def test_git_quoted_path_is_refused_instead_of_silently_passing(template):
    """git diff 預設把中文檔名轉義成 "kg/v1/\\351…"，對不上任何單向門；不能讓它默默通過。"""
    quoted = '"kg/v1/\\351\\226\\200\\346\\252\\273.json"'
    errors = check(_body(template, [NONE_LABEL]), [quoted, "README.md"])
    assert errors == [(
        f"變更檔案清單裡有被 git 轉義的路徑，無法比對單向門：{quoted}；"
        "請用 git -c core.quotePath=false diff --name-only 產生清單（檔名不能含 tab、引號等特殊字元）"
    )]


def test_ci_lists_changed_files_without_git_path_quoting(repo):
    import yaml

    workflow = yaml.safe_load((repo / ".github" / "workflows" / "pr-gate.yml").read_text(encoding="utf-8"))
    run = "\n".join(step.get("run", "") for step in workflow["jobs"]["one-way-doors"]["steps"])
    assert "git -c core.quotePath=false diff --name-only" in run
    assert "git -c core.quotePath=false diff --name-only" in (repo / "README.md").read_text(encoding="utf-8")


def test_main_reports_quoted_paths(template, tmp_path, capsys):
    body, changed = tmp_path / "body.md", tmp_path / "changed.txt"
    body.write_text(_body(template, [NONE_LABEL]), encoding="utf-8")
    changed.write_text('"kg/v1/\\351.json"\n', encoding="utf-8")
    assert pr_gate.main(["--body", str(body), "--changed", str(changed)]) == 1
    assert "被 git 轉義的路徑" in capsys.readouterr().err
