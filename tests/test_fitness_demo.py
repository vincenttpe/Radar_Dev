"""下肢三項測驗 Demo（demo/fitness_build.py，ADR 0012 提議、docs/05 E5 選項 e）。

模擬的測驗時段事件 → 量測（坐站次數、步行速度、2.44 公尺來回秒數）→ 體育署 115 年官方表判定等級，
並列出知識圖譜 v1 的結果對照。只在 Demo：不改 kg/ 與 src/ 的判定，資料全部是模擬的。
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from radar_agent.event_validation import validate_event
from radar_agent.kg_rules import assess, load

REPO = Path(__file__).resolve().parent.parent
NORMS = json.loads((REPO / "demo" / "fitness_norms_115.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def build():
    spec = importlib.util.spec_from_file_location("fitness_build", REPO / "demo" / "fitness_build.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def data(build):
    return build.fitness_data()


def _docs25_tables():
    """docs/25 Q1 來源 5 的四張表：{項目: {"男"|"女": [每個年齡組的四個下限字串]}}。"""
    text = (REPO / "docs" / "25_文獻與資料集參考.md").read_text(encoding="utf-8")
    tables = {}
    for name in NORMS["items"]:
        block = re.search(rf"^\| {name} \| 65~69 .*?\n\|---.*?\n((?:\|.*\n)+)", text, re.M)
        assert block, name
        rows = {}
        for line in block.group(1).strip().split("\n"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            rows[cells[0]] = [c.split("／") for c in cells[1:]]
        tables[name] = rows
    return tables


def _num(text):
    return float(text) if "." in text else int(text)


def test_norms_file_matches_the_table_in_docs25():
    """Demo 用的官方表和 docs/25 的表一字不差：改了一邊沒改另一邊就會失敗。"""
    tables = _docs25_tables()
    for name, item in NORMS["items"].items():
        for sex, key in (("男", "male"), ("女", "female")):
            assert [g["lower_bounds"] for g in item[key]] == [[_num(v) for v in cell] for cell in tables[name][sex]]
    assert NORMS["source"]["year"] == 115 and NORMS["source"]["group"] == "一般檢測（樂活組）"
    assert NORMS["items"]["動態平衡與敏捷力"]["direction_confirmed"] is False


def test_simulated_events_follow_the_draft_schema(build):
    events = build.simulated_events()
    assert events and all(not validate_event(e) for e in events)
    assert {e["subject_id"] for e in events} == {build.SUBJECT["id"]}


def test_chair_stand_counts_stands_that_finish_within_30_seconds(build, data):
    chair = data["tests"]["chair_stand"]
    assert chair["measure"]["value"] == "15"  # 第 16 次在 30 秒之後才站好，不算
    assert len(chair["measure"]["used"]) == 15 and len(chair["measure"]["late"]) == 1


def test_chair_stand_is_graded_by_the_official_table_for_age_and_sex(data):
    """模擬長者是女性 72 歲：115 年樂活組女 70~74 歲下肢肌力的下限 13／16／19／23，15 次是「稍差」。"""
    grade = data["tests"]["chair_stand"]["official"]
    assert (grade["status"], grade["level"], grade["pr"]) == ("graded", "稍差", "PR21-40")
    assert grade["ages"] == "70~74歲" and grade["lower_bounds"] == [13, 16, 19, 23]


@pytest.mark.parametrize(("value", "level"), [(0, "不好"), (12, "不好"), (13, "稍差"), (16, "適當"), (19, "良好"),
                                              (22, "良好"), (23, "很好"), (40, "很好")])
def test_official_level_uses_the_presumed_inclusive_lower_bounds(build, value, level):
    grade = build.official_grade("下肢肌力", "female", 72, value)
    assert grade["level"] == level


def test_items_without_a_usable_official_table_are_not_graded(build, data):
    walk = data["tests"]["up_and_go"]["official"]
    assert walk["status"] == "not_graded" and "方向待確認" in walk["reason"]
    assert data["tests"]["up_and_go"]["measure"]["value"] == "7.4"
    speed = data["tests"]["gait_speed"]["official"]
    assert speed["status"] == "not_graded" and "官方表沒有" in speed["reason"]
    assert data["tests"]["gait_speed"]["measure"]["value"] == "0.87"
    assert build.official_grade("下肢肌力", "female", 64, 15)["status"] == "not_graded"  # 樂活組只有 65 歲以上


def test_low_confidence_session_is_not_assessed(data):
    """量測限制：測驗時段裡有信心值不足的事件，次數不可靠，寫「無法評估」，不出等級。"""
    retry = data["tests"]["chair_stand_retry"]
    assert retry["measure"]["value"] is None and "信心值" in retry["measure"]["reason"]
    assert retry["official"]["status"] == "not_graded" and retry["v1"] is None


def test_v1_result_is_shown_side_by_side_from_kg_rules(data):
    kg = load()
    for key, metric in (("chair_stand", "下肢肌力"), ("up_and_go", "來回行走"), ("gait_speed", "步行速度")):
        test = data["tests"][key]
        expected = assess(kg, metric, test["measure"]["value"])
        assert test["v1"] == {k: expected[k] for k in ("status", "score", "rank", "source_text", "reason")}


def test_committed_page_is_exactly_what_fitness_build_produces(build, data):
    committed = (REPO / "demo" / "fitness_demo.html").read_text(encoding="utf-8")
    assert committed == build.page(data), "改了 fitness_template.html、官方表或模擬資料要重跑 python demo/fitness_build.py"


def test_page_is_labelled_as_simulated_draft(build, data):
    page = build.page(data)
    for label in ("模擬資料・內部用", "草案", "推定", "115 年", "一般檢測（樂活組）", "方向待確認"):
        assert label in page, label


def _session(build, key):
    return next(s for s in build.SESSIONS if s["key"] == key)


def test_gait_speed_needs_exactly_one_walk_in_the_session(build):
    session = _session(build, "gait_speed")
    walk = next(e for e in build.simulated_events() if e["label"] == "walking"
                and e["timestamps"]["started"] < build._iso(session["started"] + build._s(60)))
    for events in ([], [walk, {**walk, "event_id": "SIM-FT-X"}]):
        got = build.measure(session, events)
        assert got["value"] is None and f"有 {len(events)} 筆行走事件" in got["reason"]


def test_up_and_go_without_sitting_back_down_is_not_assessed(build):
    session = _session(build, "up_and_go")
    events = [e for e in build.simulated_events() if e["label"] != "stand_to_sit"]
    got = build.measure(session, events)
    assert got["value"] is None and "沒有站→坐事件" in got["reason"]


def test_unknown_test_key_is_refused(build):
    with pytest.raises(ValueError, match="不認得的測驗"):
        build.measure({**_session(build, "up_and_go"), "key": "six_minute_walk"}, build.simulated_events())


def test_norms_age_groups_match_the_table_header_in_docs25():
    text = (REPO / "docs" / "25_文獻與資料集參考.md").read_text(encoding="utf-8")
    for name, item in NORMS["items"].items():
        header = re.search(rf"^\| {name} \| (.*) \|$", text, re.M).group(1).split(" | ")
        ages = [g["ages"].removesuffix("歲").replace("90歲以上", "90 以上") for g in item["male"]]
        assert [h.replace(" ", "") for h in header] == [a.replace(" ", "") for a in ages]
        assert [g["ages"] for g in item["male"]] == [g["ages"] for g in item["female"]]


def test_page_says_the_two_scales_cannot_be_compared_directly(build, data):
    page = build.page(data)
    assert "名稱與分級方式不同，不能直接比較" in page
    assert "FT-DRAFT-0 自己定的" in page
