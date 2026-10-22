"""真人試測用紙 demo/usability_kit.html：內容來自 docs/17，docs 改了沒重新產生就會失敗。"""
import importlib.util
import re

import pytest

from radar_agent.output_check import wording_problems


@pytest.fixture(scope="module")
def kit(repo):
    spec = importlib.util.spec_from_file_location("usability_kit", repo / "demo" / "usability_kit.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_kit_is_exactly_what_the_script_produces(repo, kit):
    committed = (repo / "demo" / "usability_kit.html").read_text(encoding="utf-8")
    assert committed == kit.page(kit.load_plan()), "改了 docs/17 的任務或 SUS 要重跑 python demo/usability_kit.py"


def test_every_task_in_docs_17_has_a_row_for_both_interfaces(repo, kit):
    plan = kit.load_plan()
    docs = (repo / "docs" / "17_介面易用性評估.md").read_text(encoding="utf-8")
    ids = re.findall(r"^\| ([A-Z]\d+) \| ", docs.split("### 任務", 1)[1].split("### 記錄表", 1)[0], re.M)
    assert sorted(t["id"] for rows in plan["tasks"].values() for t in rows) == sorted(ids)
    html = kit.page(plan)
    assert html.count('<td class="ui">官網</td>') == html.count('<td class="ui">試做頁</td>') == len(ids)


def test_answer_key_stays_off_the_participant_questionnaire(kit):
    """SUS 那一頁是給受測者填的：不能出現任何任務的「做對的標準」。"""
    plan = kit.load_plan()
    html = kit.page(plan)
    questionnaire = html.split("<h2>使用感受問卷（SUS）</h2>", 1)[1]
    assert all(t["done"] not in questionnaire for rows in plan["tasks"].values() for t in rows)
    assert questionnaire.count('<span class="circle">') == 5 * kit.SUS_ITEMS


def test_kit_asks_for_a_code_not_a_name_and_passes_the_wording_check(kit):
    html = kit.page(kit.load_plan())
    assert "受測者代號" in html and "姓名" not in html.replace("不寫姓名", "")
    text = re.sub(r"<[^>]+>", " ", html.split("<main>", 1)[1])
    assert not wording_problems(text)


def test_malformed_docs_stop_the_build(kit, tmp_path, repo):
    docs = (repo / "docs" / "17_介面易用性評估.md").read_text(encoding="utf-8")
    broken = tmp_path / "17.md"
    broken.write_text(docs.replace("10. 我需要先學很多東西", "我需要先學很多東西"), encoding="utf-8")
    with pytest.raises(ValueError, match="SUS 應有 10 題"):
        kit.load_plan(broken)
    broken.write_text(docs.replace("| F1 | 家屬 |", "| F1 | 居服員 |"), encoding="utf-8")
    with pytest.raises(ValueError, match="讀者對不上"):
        kit.load_plan(broken)
