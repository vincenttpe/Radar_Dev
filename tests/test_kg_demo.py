"""知識圖譜 Demo 頁 demo/kg_demo.html（docs/11「知識圖譜 Demo」）。

頁面上的等級必須是知識圖譜門檻給的、居服員看的句子必須是 rewrite() 的輸出；
模擬事件要符合事件格式草案，換算規則與資料一改，沒重新產生就會失敗。
"""
import importlib.util
import json
import re
import sys
from decimal import Decimal

import pytest

from radar_agent.caregiver import FORBIDDEN, check_output
from radar_agent.event_validation import validate_event
from radar_agent.kg_rules import assess, load
from radar_agent.rewrite import rewrite


@pytest.fixture(scope="module")
def build(repo):
    spec = importlib.util.spec_from_file_location("kg_demo_build", repo / "demo" / "kg_build.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass 要從 sys.modules 找到模組
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def data(build):
    return build.kg_data()


def test_committed_page_is_exactly_what_kg_build_produces(repo, build, data):
    committed = (repo / "demo" / "kg_demo.html").read_text(encoding="utf-8")
    assert committed == build.page(data), "改了 kg_template.html、kg_build.py 或用語要重跑 python demo/kg_build.py"


def test_simulated_events_follow_the_draft_schema(build):
    events = build.simulated_events()
    assert events and all(validate_event(e) == [] for e in events)
    assert len({e["event_id"] for e in events}) == len(events)
    assert {e["device_id"] for e in events} == {"DEMO-SIM-KG-01"}
    assert {e["subject_id"] for e in events} == {"DEMO-ELDER-01"}


def test_walking_speed_follows_the_draft_rule(build):
    events = build.simulated_events()
    speed = build.walking_speed(events)
    walks = [e for e in events if e["label"] == "walking"]
    assert speed["walks"] == len(walks) == len(speed["used"]) + len(speed["short"]) + len(speed["unsure"])
    for e in walks:
        if e["event_id"] in speed["used"]:
            assert e["confidence"] >= 0.8 and build._seconds(e) >= 5 and e["measurements"][0]["value"] >= 3
    assert speed["short"] and speed["unsure"], "模擬資料要有被排除的行走，才看得出規則在做事"


def test_too_few_walks_is_not_assessed(build):
    events = build.simulated_events()
    speed = build.walking_speed([e for e in events if e["event_id"] in build.walking_speed(events)["used"][:9]])
    assert speed["value"] is None


def test_level_comes_from_the_knowledge_graph(data):
    expected = assess(load(), "步行速度", data["speed"]["value"])
    assert expected["status"] == "assessed"
    assert (data["assessment"]["score"], data["assessment"]["rank"]) == (expected["score"], expected["rank"])
    assert Decimal("0.8") < Decimal(data["speed"]["value"]) < Decimal("1.0")


def test_caregiver_text_is_rewrite_output_and_passes_checks(data):
    result = assess(load(), "步行速度", data["speed"]["value"])
    assert data["caregiver"] == rewrite(result).text == data["dialogue"][0]["a"]
    assert check_output(data["caregiver"], result) == []
    assert "僅供測試" in data["caregiver"]


def test_boundary_value_is_not_assessed(data):
    boundary = data["boundary"]
    assert boundary["value"] == "0.80"
    assert "無法評估" in boundary["caregiver"] and "重疊" in boundary["reason"]
    assert "重疊" not in boundary["caregiver"], "缺口、重疊等審核用語不給居服員"


def test_every_answer_has_basis_and_no_forbidden_words(data):
    assert len(data["dialogue"]) == 4
    for turn in data["dialogue"]:
        assert turn["basis"]
        assert not [w for w in FORBIDDEN if w in turn["a"]]
    assert "草案" in data["dialogue"][1]["a"]
    assert data["dialogue"][2]["a"].count("無法評估") == 2
    assert "不回答" in data["dialogue"][3]["a"]


def test_draft_rule_change_must_keep_page_in_sync(build, monkeypatch):
    monkeypatch.setattr(build, "RULE", build.Rule(min_meters=Decimal("100")))
    with pytest.raises(ValueError, match="合格行走次數不足"):
        build.kg_data()


def test_page_says_simulated_and_draft(repo):
    page = (repo / "demo" / "kg_demo.html").read_text(encoding="utf-8")
    banner = re.search(r'<div class="sim">(.*?)</div></div>', page, re.S).group(1)
    assert "模擬資料" in banner
    assert "DEMO-DRAFT-0" in page and "草案，未經 PT 審核" in page


def test_embedded_data_cannot_close_the_script_tag(build, data):
    html = build.fragment({**data, "caregiver": "</script><b>"})
    payload = html.split("const DATA = ", 1)[1].split(";\n", 1)[0]
    assert "</script>" not in payload and json.loads(payload)["caregiver"] == "</script><b>"
