"""交班重點（草案，Jetson 內部 Demo 的實驗功能，docs/21）：原句是【重點】加【事件摘要】，在地模型只改寫事件摘要。

原句用 Demo 的模擬資料（一間浴廁一個上午）；模型以假模型代替（外部服務的邊界，REVIEW.md 3.5）。
GOOD_BODY 是 2026-10-29 Gemma 4 E4B（Q4_K_M）實際改寫的輸出，用來確認真實的口語版會被採用。
"""
from pathlib import Path

import pytest
from test_caregiver import timeline
from test_rewrite import FakeModel, rule_digest

from radar_agent.caregiver import caregiver_text, handover_text, highlights
from radar_agent.device_events import read_records, summarize
from radar_agent.output_check import check_output
from radar_agent.rewrite import HANDOVER_PROMPT_VERSION, rewrite_handover

PLACE = "1 號廁所"
DEMO = summarize(read_records(Path(__file__).resolve().parent.parent / "data" / "samples_demo" / "bathroom_morning.jsonl"))
HEAD = "1 號廁所 10/14 08:00–11:59：交班重點如下。\n" + highlights(DEMO)
GOOD_BODY = (
    "1 號廁所，10/14 有人使用 5 次，最長一次是 22 分鐘。\n"
    "在線期間跌倒警報有 2 次：08:32 時語音詢問了 10 秒沒回應，30 秒後長者回覆沒事，警報就解除；"
    "09:16 長者回覆需要協助，3 分 10 秒後偵測到起身，警報也解除。\n"
    "在線期間主動呼救有 1 次，是 10:03，2 分 35 秒後偵測到起身，警報解除。\n"
    "在線期間久待警報有 1 次，發生在 10:45。\n"
    "11:10 到 11:25 裝置斷線，這段時間無法判斷是否有跌倒。"
)


def test_template_is_the_highlights_and_the_event_summary_under_a_fixed_heading():
    text = handover_text(DEMO, place=PLACE)
    assert text == f"{HEAD}\n{caregiver_text(DEMO, place=PLACE)}"
    assert check_output(text, DEMO, place=PLACE) == []


def test_template_without_alerts_still_has_highlights():
    quiet = summarize(timeline("09:00:00", "09:30:00", [("09:05:00", "09:13:00")]))
    assert "【重點】這段時間沒有警報，裝置都在線。" in handover_text(quiet, place=PLACE).splitlines()


def test_template_without_data_cannot_tell_keeps_highlights_and_is_never_sent_to_the_model():
    model = FakeModel(GOOD_BODY)
    result = rewrite_handover(summarize([]), place=PLACE, model=model)
    assert (result.text, result.layer) == (
        f"{PLACE}：沒有收到資料，無法判斷。\n【重點】沒有收到資料，無法判斷，請通知資訊人員。", "template"
    )
    assert model.prompts == []


def test_without_a_model_the_template_is_used():
    result = rewrite_handover(DEMO, place=PLACE)
    assert (result.text, result.layer, result.rejected) == (handover_text(DEMO, place=PLACE), "template", ())


def test_a_faithful_spoken_version_is_used_under_the_fixed_heading_and_highlights():
    result = rewrite_handover(DEMO, place=PLACE, model=FakeModel(GOOD_BODY))
    assert (result.text, result.layer, result.model, result.prompt_version, result.rejected) == (
        f"{HEAD}\n{GOOD_BODY}", "model", "fake-0.1", HANDOVER_PROMPT_VERSION, ()
    )


def test_the_prompt_carries_the_event_summary_but_not_the_heading_or_highlights():
    """模型實測會把開頭與【重點】整行省掉（數字在事件摘要裡都還有，檢查擋不到），所以這兩行不交給模型。"""
    model = FakeModel(GOOD_BODY)
    rewrite_handover(DEMO, place=PLACE, model=model)
    prompt = model.prompts[0]
    assert caregiver_text(DEMO, place=PLACE) in prompt
    assert "交班重點如下" not in prompt and "【重點】" not in prompt


@pytest.mark.parametrize("reply, reason", [
    (GOOD_BODY.replace("在線期間久待警報有 1 次，發生在 10:45。\n", ""), "漏掉時間 10:45"),
    # 時段詞說錯與「我們」：2026-10-29 Gemma 4 實際寫出來過
    (GOOD_BODY.replace("是 10:03", "是下午 10:03"), "範本沒有「下午」"),
    (GOOD_BODY.replace("語音詢問了 10 秒", "我們語音詢問了 10 秒"), "範本沒有「我們」"),
    (GOOD_BODY.replace("這段時間無法判斷是否有跌倒", "這段時間狀況不明"), "漏掉「無法判斷」"),
    # 次數、時間長度改掉：數字可能在別處還有（只比數字會放過），要靠「N 次／分鐘／秒」整組比對
    (GOOD_BODY.replace("使用 5 次", "使用 6 次"), "漏掉「5次」"),
    (GOOD_BODY.replace("22 分鐘", "20 分鐘"), "範本沒有「20分」"),
    (GOOD_BODY.replace("35 秒", "25 秒"), "漏掉「2分35秒」"),
    # 時間長度的「分」改掉：2、3 在別處都還有，「10 秒」也還在，要整段比（2026-10-29 審查實測）
    (GOOD_BODY.replace("3 分 10 秒", "2 分 10 秒"), "漏掉「3分10秒」"),
    (GOOD_BODY.replace("2 分 35 秒", "3 分 35 秒"), "範本沒有「3分35秒」"),
])
def test_unfaithful_rewrites_fall_back_to_the_template(reply, reason):
    result = rewrite_handover(DEMO, place=PLACE, model=FakeModel(reply))
    assert result.layer == "template" and result.text == handover_text(DEMO, place=PLACE)
    assert reason in result.rejected


def test_minutes_written_with_or_without_zhong_count_as_the_same_duration():
    result = rewrite_handover(DEMO, place=PLACE, model=FakeModel(GOOD_BODY.replace("22 分鐘", "22 分")))
    assert result.layer == "model"


def test_model_errors_fall_back_to_the_template():
    result = rewrite_handover(DEMO, place=PLACE, model=FakeModel(error=TimeoutError()))
    assert result.layer == "template" and result.rejected == ("模型錯誤：TimeoutError",)


def test_a_template_that_fails_its_own_check_is_not_handed_to_the_model():
    model = FakeModel(GOOD_BODY)
    with pytest.raises(ValueError, match="範本沒通過輸出檢查"):
        rewrite_handover(DEMO, place="治療室", model=model)
    assert model.prompts == []


def _with_unreadable():
    """有人待 75 分鐘、另有 1 筆讀不懂的紀錄。"""
    records = timeline("09:00:00", "10:30:00", [("09:05:00", "10:20:00")])
    records[10] = {**records[10], "payload": "壞掉"}
    return summarize(records)


def test_dropping_the_unreadable_record_line_falls_back():
    """「另有 1 筆紀錄無法判讀」整行刪掉：【重點】還寫著，但事件摘要不能少（「1 筆」整組比對）。"""
    summary = _with_unreadable()
    body = handover_text(summary, place=PLACE).split("\n", 2)[2]
    reply = body.replace("\n另有 1 筆紀錄無法判讀，請通知資訊人員。", "")
    result = rewrite_handover(summary, place=PLACE, model=FakeModel(reply))
    assert result.layer == "template" and "漏掉「1筆」" in result.rejected


def test_known_limit_b_none_sentence_turned_positive_is_not_caught():
    """已知限制（docs/21，2026-10-29 決定靠並排對照找出）：「沒有……」改成「有……」沒有數字可比，檢查放過。"""
    summary = _with_unreadable()
    body = handover_text(summary, place=PLACE).split("\n", 2)[2]
    reply = body.replace("沒有跌倒警報", "有跌倒警報")
    assert rewrite_handover(summary, place=PLACE, model=FakeModel(reply)).layer == "model"


def test_known_limit_a_a_count_moved_to_another_event_is_not_caught():
    """已知限制（docs/21）：次數只比「這個數量有沒有出現」，主動呼救 1 次改成 2 次時，2 次在跌倒那行本來就有。"""
    reply = GOOD_BODY.replace("主動呼救有 1 次", "主動呼救有 2 次")
    assert rewrite_handover(DEMO, place=PLACE, model=FakeModel(reply)).layer == "model"


def test_known_limit_a_swapped_durations_are_not_caught():
    """已知限制（docs/21）：兩次警報的時間長度對調，每個時間長度都還在，檢查放過。"""
    reply = (GOOD_BODY.replace("3 分 10 秒", "<X>").replace("2 分 35 秒", "3 分 10 秒").replace("<X>", "2 分 35 秒"))
    assert rewrite_handover(DEMO, place=PLACE, model=FakeModel(reply)).layer == "model"


def test_prompt_version_is_bumped_when_the_prompt_or_checks_change():
    """紀錄只記 prompt_version；交班的 prompt 或採用的檢查改了卻沒升版，就分不出一筆退回是哪一套規則（原則 3）。"""
    from radar_agent import rewrite as r

    digest = rule_digest(r.build_handover_prompt("{原句}"), r.rewrite_handover, r.checked_rewrite, r._kept_facts, r._quantities,
                         r._kept_quantities, r._clock_times, r._kept_times, r._added_words, r._QUANTITY.pattern,
                         r._CLOCK.pattern, r.REQUIRED_PHRASES, r.MAX_LENGTH_RATIO, r.HANDOVER_ADDED_WORDS)
    assert (HANDOVER_PROMPT_VERSION, digest) == ("handover-v0", "1a8a0d11eb38d6ae"), \
        "rewrite.build_handover_prompt 或採用的檢查改了：升 rewrite.HANDOVER_PROMPT_VERSION，再更新這裡的版本與雜湊"
