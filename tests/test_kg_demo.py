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

from radar_agent.audience import ALLOWED
from radar_agent.output_check import FORBIDDEN, check_output
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
    assert data["caregiver"] == rewrite(result).text == _menu(data, "居服員")[0]["a"]
    assert check_output(data["caregiver"], result) == []
    assert "僅供測試" in data["caregiver"]


def test_boundary_value_is_not_assessed(data):
    boundary = data["boundary"]
    assert boundary["value"] == "0.80"
    assert "無法評估" in boundary["caregiver"] and "重疊" in boundary["reason"]
    assert "重疊" not in boundary["caregiver"], "缺口、重疊等審核用語不給居服員"


def _menu(data, profile):
    return next(p for p in data["profiles"] if p["profile"] == profile)["questions"]


def test_profiles_follow_audience_permissions(data):
    assert [p["profile"] for p in data["profiles"]] == list(ALLOWED)
    for p in data["profiles"]:
        if "個人" in ALLOWED[p["profile"]]:
            assert not p["refused"] and p["questions"]
        else:
            assert p["refused"] and not p["questions"], "不能看個人資料的讀者不能有題目"


def test_every_answer_has_basis_follow_ups_and_no_forbidden_words(data):
    for p in data["profiles"]:
        ids = {q["id"] for q in p["questions"]}
        for q in p["questions"]:
            assert q["basis"] and set(q["follow"]) <= ids
            assert not [w for w in FORBIDDEN if w in q["a"]]
        if p["questions"]:
            assert p["questions"][-1]["id"] == "out" and "不回答" in p["questions"][-1]["a"]


def test_only_caregiver_uses_the_formal_template(data):
    result = assess(load(), "步行速度", data["speed"]["value"])
    for p in data["profiles"]:
        for q in p["questions"]:
            if q["checked"]:
                assert check_output(q["a"], result) == []
            if p["profile"] != "居服員" and q["id"] != "out":
                assert q["draft"], f"{p['profile']}還沒有正式範本，要標草案"
    caregiver = _menu(data, "居服員")
    assert not any(q["draft"] for q in caregiver)
    assert "草案" in caregiver[1]["a"] and caregiver[2]["a"].count("無法評估") == 2


def test_review_wording_only_reaches_doctors(data):
    for p in data["profiles"]:
        mentions = any("重疊" in q["a"] for q in p["questions"])
        assert mentions == (p["profile"] == "醫師"), "門檻重疊等審核用語只給醫師"


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


# ---- 測試值試算：只換輸入，知識圖譜不變；頁面只查表，不自己判定等級 ----

def _tester(data, metric):
    return next(m for m in data["tester"]["metrics"] if m["metric"] == metric)


def _point(entry, value):
    """照頁面查表的方式，還原某個測試值的結果。"""
    outcome = entry["outcomes"][int(entry["codes"][int(Decimal(value) / Decimal(entry["step"]))])]
    head = f'{entry["metric"]} {value} {entry["unit"]}'
    reason = f'數值 {value} {outcome["reason"]}' if outcome["reason"] else None
    return outcome, head + outcome["rest"], reason


def test_tester_covers_every_metric_in_the_knowledge_graph(data):
    assert {m["metric"] for m in data["tester"]["metrics"]} == set(load().thresholds)


def test_tester_table_is_exactly_what_the_engine_produces(data):
    kg = load()
    for entry in data["tester"]["metrics"]:
        step = Decimal(entry["step"])
        assert len(entry["codes"]) == int(Decimal(entry["max"]) / step) + 1
        for k in range(len(entry["codes"])):
            value = str(Decimal(k) * step)
            expected = assess(kg, entry["metric"], value)
            outcome, text, reason = _point(entry, value)
            assert text == rewrite(expected).text and check_output(text, expected) == []
            assert reason == expected["reason"]
            assert (outcome["status"], outcome["score"], outcome["rank"], outcome["source_text"]) == (
                expected["status"], expected["score"], expected["rank"], expected["source_text"])


@pytest.mark.parametrize("metric, value, status, score", [
    ("步行速度", "0.79", "assessed", 1),
    ("步行速度", "0.80", "unassessable", None),  # 門檻重疊
    ("步行速度", "0.81", "assessed", 2),
    ("下肢肌力", "12", "assessed", 2),
    ("心肺耐力", "105", "unassessable", None),   # 門檻缺口
    ("來回行走", "5.45", "unassessable", None),  # 比來源精度細
])
def test_tester_can_reach_every_kind_of_result(data, metric, value, status, score):
    outcome, _, _ = _point(_tester(data, metric), value)
    assert (outcome["status"], outcome["score"]) == (status, score)


def test_regenerated_answers_follow_the_test_value(data):
    """重新生成後，問答要跟著測試值走：等級來自引擎，事件才算得出的說明不能沿用。"""
    speed = _tester(data, "步行速度")
    outcome, text, _ = _point(speed, "0.3")
    answers = outcome["answers"]
    assert answers["居服員"]["walk"]["a"].replace("{v}", "0.3") == text
    assert "「不好」" in answers["家屬"]["walk"]["a"] and "0 分「不好」" in answers["醫師"]["value"]["a"]
    assert "34" not in answers["居服員"]["how"]["a"] and "不是由事件換算" in answers["居服員"]["how"]["a"]
    assert "0.90" not in answers["居服員"]["how"]["q"]
    assert "僅供測試" in answers["家屬"]["walk"]["a"]


def test_regenerated_answers_cover_only_questions_in_the_menu(data):
    menus = {p["profile"]: {q["id"] for q in p["questions"]} for p in data["profiles"]}
    for entry in data["tester"]["metrics"]:
        for outcome in entry["outcomes"]:
            assert set(outcome["answers"]) == {"居服員", "醫師", "家屬", "長者本人"}
            for profile, answers in outcome["answers"].items():
                assert set(answers) <= menus[profile]


def test_regenerated_answers_do_not_guess_a_level_when_unassessable(data):
    outcome, _, _ = _point(_tester(data, "步行速度"), "0.80")
    answers = outcome["answers"]
    for profile, qid in (("家屬", "walk"), ("家屬", "advice"), ("長者本人", "walk"), ("長者本人", "advice")):
        text = answers[profile][qid]["a"]
        assert "無法評估" in text and "稍差" not in text and "普通" not in text and "None" not in text
    assert "重疊" in answers["醫師"]["value"]["a"], "審核用語只給醫師"
    for profile in ("居服員", "家屬", "長者本人"):
        assert not any("重疊" in a.get("a", "") for a in answers[profile].values())


def test_simulated_only_answers_say_they_do_not_follow_the_test_value(data):
    """頁首寫「目前顯示測試值」時，答案仍來自模擬事件的題目要標明，不能看起來像測試值的結果。"""
    menus = {p["profile"]: {q["id"]: q["a"] for q in p["questions"]} for p in data["profiles"]}
    for entry in data["tester"]["metrics"]:
        for outcome in entry["outcomes"]:
            for profile, qid in (("居服員", "others"), ("醫師", "rule"), ("醫師", "unassessed")):
                note = outcome["answers"][profile][qid]
                assert "模擬資料" in note["note"] and "a" not in note
    assert "56 次" in menus["居服員"]["others"], "這題確實引用模擬事件的筆數"


def test_every_metric_regenerates_the_answers_with_its_own_wording(data):
    """每項指標都能換問答；題目與句子要講那項指標，不能還在問走路。"""
    for entry in data["tester"]["metrics"]:
        metric = entry["metric"]
        for outcome in entry["outcomes"]:
            answers = outcome["answers"]
            assert metric in answers["居服員"]["walk"]["q"] and metric in answers["長者本人"]["walk"]["q"]
            assert metric in answers["家屬"]["walk"]["a"] and metric in answers["長者本人"]["walk"]["a"]
            assert metric in answers["醫師"]["value"]["a"]
    answers = _point(_tester(data, "下肢肌力"), "12")[0]["answers"]
    assert "這週下肢肌力" in answers["居服員"]["walk"]["q"] and "走" not in answers["居服員"]["walk"]["q"]
    assert "「普通」" in answers["家屬"]["walk"]["a"] and "q" not in answers["家屬"]["walk"], "家屬的題目本來就不限於走路"
    for profile, qid in (("家屬", "walk"), ("長者本人", "walk"), ("居服員", "how")):
        assert "走路" not in answers[profile][qid]["a"] and "行走紀錄" not in answers[profile][qid]["a"]


def test_internal_page_has_a_regenerate_button(repo):
    page = (repo / "demo" / "kg_demo.html").read_text(encoding="utf-8")
    assert "重新生成" in page and "回到模擬資料" in page


def test_tester_explains_that_test_values_are_not_sent_for_review(repo):
    """「已交由審核人確認」是正式句子的固定用語，試算時不成立，頁面要講清楚。"""
    page = (repo / "demo" / "kg_demo.html").read_text(encoding="utf-8")
    assert "測試值不會送審" in page and "非真實長者" in page


def test_tester_rejects_a_metric_outside_the_knowledge_graph(build, monkeypatch):
    monkeypatch.setitem(build.TEST_RANGES, "握力", ("100", "1"))
    with pytest.raises(ValueError, match="握力"):
        build.tester_data(load())


# ---- 客戶版（功能預告，demo/jetson_serve.py 的 /kg）----

PREVIEW_BANNER = "功能預告・開發中：知識圖譜的換算規則與句子尚未完成專業審核；畫面資料是模擬的，不是真實長者。"


def test_preview_page_says_it_is_a_preview_in_development(build, data):
    page = build.preview_page(data)
    assert PREVIEW_BANNER in page
    assert "知識圖譜（功能預告）" in page and 'href="/"' in page  # 回到 Demo 主頁


def test_preview_page_keeps_only_the_five_step_flow(build, data):
    page = build.preview_page(data)
    assert 'id="flow"' in page and "虛線框是還沒定案的部分" in page
    for internal in ("內部用", "內部確認功能用", "還缺什麼", "展示時要說明", "誰提供", "週一清單",
                     "誰在問，能問什麼", "五項指標", "邊界情境", "事件明細", 'id="sim-note"',
                     "測試值", 'id="try-metric"', "DATA.tester", "重新生成", "回到模擬資料"):
        assert internal not in page, internal


def test_preview_page_carries_only_the_data_the_flow_needs(build, data):
    """頁面原始碼裡也不帶問答、審核用的原因與事件明細：客戶看原始碼也看不到內部內容。"""
    page = build.preview_page(data)
    shipped = json.loads(re.search(r"const DATA = (\{.*?\n\});", page, re.S).group(1).replace("<\\/", "</"))
    assert set(shipped) == set(build.PREVIEW_KEYS)
    assert shipped["events"] == [{"label": e["label"]} for e in data["events"]]
    sp = data["speed"]  # 量測值只帶數值與筆數，不帶事件編號
    assert shipped["speed"] == {"value": sp["value"], "used": len(sp["used"]), "short": len(sp["short"]),
                                "unsure": len(sp["unsure"])}
    assert shipped["assessment"] == {k: data["assessment"][k] for k in ("unit", "score", "rank", "source_text")}
    for key in ("caregiver", "kg", "rule", "period"):
        assert shipped[key] == data[key]
    assert not any(e["id"] in page for e in data["events"])
    assert "審核用的原因" not in page and data["boundary"]["reason"] not in page


def test_preview_page_lede_does_not_promise_answers_it_does_not_show(build, data):
    page = build.preview_page(data)
    assert "回答照護人員的問題" not in page and "最後產生照護人員看的句子。</p>" in page
    assert "等級只由知識圖譜的門檻決定" not in page  # 屬於拿掉的「展示時要說明」，不放回導言
    assert "怎麼變成照護人員看的句子" in page and "一段回答" not in page
    assert "rewrite()" not in page and "固定範本產生，已通過輸出檢查" in page


def test_internal_page_is_unchanged_by_the_preview(build, data):
    page = build.page(data)
    assert "模擬資料・內部用" in page and "還缺什麼" in page and PREVIEW_BANNER not in page
