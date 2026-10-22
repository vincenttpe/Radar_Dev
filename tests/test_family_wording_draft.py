"""家屬版用語草案 docs/18：草案欄的每一句都要通過用語檢查（文件裡這樣寫，這裡守著）。"""
import re

from radar_agent.output_check import wording_problems


def test_every_drafted_sentence_passes_the_wording_check(repo):
    text = (repo / "docs" / "18_家屬版用語草案.md").read_text(encoding="utf-8")
    table = text.split("## 對照表", 1)[1].split("\n## ", 1)[0]
    rows = [r.split(" | ") for r in re.findall(r"^\| (\d+ \| .+) \|$", table, re.M)]
    assert len(rows) >= 15
    drafts = [cells[3] for cells in rows]
    assert all(d.strip() for d in drafts)
    assert [d for d in drafts if wording_problems(d)] == []
