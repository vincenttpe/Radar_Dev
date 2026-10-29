"""交班重點（草案，Jetson 內部 Demo 的實驗功能，docs/21）：規則產生範本，在地模型只改寫成口語。

範本用 Demo 的模擬資料（一間浴廁一個上午）；模型以假模型代替（外部服務的邊界，REVIEW.md 3.5）。
GOOD_BODY 是 2026-10-29 Gemma 4 E4B（Q4_K_M）實際改寫的輸出，用來確認真實的口語版會被採用。
"""
from pathlib import Path

import pytest
from test_caregiver import timeline
from test_rewrite import FakeModel

from radar_agent.caregiver import handover_text
from radar_agent.device_events import read_records, summarize
from radar_agent.output_check import check_output
from radar_agent.rewrite import HANDOVER_PROMPT_VERSION, rewrite_handover

PLACE = "1 號廁所"
DEMO = summarize(read_records(Path(__file__).resolve().parent.parent / "data" / "samples_demo" / "bathroom_morning.jsonl"))
HEAD = "1 號廁所 10/14 08:00–11:59：交班重點如下。"
GOOD_BODY = (
    "08:32 1 號廁所雷達偵測到疑似跌倒，語音詢問 10 秒沒回應。接著 08:32 長者語音回覆沒事，警報解除。\n"
    "09:16 1 號廁所雷達又偵測到疑似跌倒，長者語音說需要協助。09:19 偵測到長者起身，警報解除。\n"
    "10:03 1 號廁所偵測到主動呼救。10:05 偵測到長者起身，警報解除。\n"
    "10:45 1 號廁所停留超過設定時間（久待警報）。\n"
    "使用情形是有人使用 5 次，最長一次是 22 分鐘。\n"
    "11:10 到 11:25 裝置斷線，這段時間無法判斷是否有跌倒。"
)


def test_template_lists_every_event_in_time_order_then_usage_and_offline():
    text = handover_text(DEMO, place=PLACE)
    assert text.splitlines() == [
        HEAD,
        "事件（依時間）：",
        "08:32 1 號廁所雷達偵測到疑似跌倒，語音詢問 10 秒未獲回應。",
        "08:32 長者語音回覆沒事，警報解除。",
        "09:16 1 號廁所雷達偵測到疑似跌倒，長者語音回覆需要協助。",
        "09:19 偵測到長者起身，警報解除。",
        "10:03 1 號廁所偵測到主動呼救。",
        "10:05 偵測到長者起身，警報解除。",
        "10:45 1 號廁所停留超過設定時間（久待警報）。",
        "使用情形：有人使用 5 次，最長一次 22 分鐘。",
        "11:10–11:25 裝置斷線，這段時間無法判斷是否有跌倒。",
    ]
    assert check_output(text, DEMO, place=PLACE) == []


def test_template_without_alerts_says_so():
    quiet = summarize(timeline("09:00:00", "09:30:00", [("09:05:00", "09:13:00")]))
    assert "這段時間沒有警報。" in handover_text(quiet, place=PLACE).splitlines()


def test_template_without_data_cannot_tell_and_is_never_sent_to_the_model():
    model = FakeModel(GOOD_BODY)
    result = rewrite_handover(summarize([]), place=PLACE, model=model)
    assert (result.text, result.layer) == (f"{PLACE}：沒有收到資料，無法判斷。", "template")
    assert model.prompts == []


def test_without_a_model_the_template_is_used():
    result = rewrite_handover(DEMO, place=PLACE)
    assert (result.text, result.layer, result.rejected) == (handover_text(DEMO, place=PLACE), "template", ())


def test_a_faithful_spoken_version_is_used_and_keeps_the_fixed_first_line():
    result = rewrite_handover(DEMO, place=PLACE, model=FakeModel(GOOD_BODY))
    assert (result.text, result.layer, result.model, result.prompt_version, result.rejected) == (
        f"{HEAD}\n{GOOD_BODY}", "model", "fake-0.1", HANDOVER_PROMPT_VERSION, ()
    )


def test_the_prompt_carries_the_events_but_not_the_fixed_first_line():
    model = FakeModel(GOOD_BODY)
    rewrite_handover(DEMO, place=PLACE, model=model)
    prompt = model.prompts[0]
    assert "10:45 1 號廁所停留超過設定時間（久待警報）。" in prompt
    assert HEAD not in prompt


@pytest.mark.parametrize("reply, reason", [
    # 漏掉 10:05 那件事：10 和 5 在別處都還有（10:03、5 次），只比數字會放過，要靠時刻檢查
    (GOOD_BODY.replace("10:05 偵測到長者起身，警報解除。", ""), "漏掉時間 10:05"),
    # 時段詞說錯（10:03 是上午）與「我們」：2026-10-29 Gemma 4 實際寫出來過
    (GOOD_BODY.replace("10:03 1 號廁所", "下午 10:03 1 號廁所"), "範本沒有「下午」"),
    (GOOD_BODY.replace("語音詢問 10 秒", "我們語音詢問了 10 秒"), "範本沒有「我們」"),
    (GOOD_BODY.replace("這段時間無法判斷是否有跌倒", "這段時間狀況不明"), "漏掉「無法判斷」"),
    # 次數改掉：5 還在「10:05」、6 也是別的事實（只比數字會放過），要靠「N 次」整組比對
    (GOOD_BODY.replace("5 次", "6 次"), "漏掉「5 次」"),
    (GOOD_BODY.replace("22 分鐘", "20 分鐘"), "範本沒有「20 分鐘」"),
])
def test_unfaithful_rewrites_fall_back_to_the_template(reply, reason):
    result = rewrite_handover(DEMO, place=PLACE, model=FakeModel(reply))
    assert result.layer == "template" and result.text == handover_text(DEMO, place=PLACE)
    assert reason in result.rejected


def test_model_errors_fall_back_to_the_template():
    result = rewrite_handover(DEMO, place=PLACE, model=FakeModel(error=TimeoutError()))
    assert result.layer == "template" and result.rejected == ("模型錯誤：TimeoutError",)


def test_a_template_that_fails_its_own_check_is_not_handed_to_the_model():
    model = FakeModel(GOOD_BODY)
    with pytest.raises(ValueError, match="範本沒通過輸出檢查"):
        rewrite_handover(DEMO, place="治療室", model=model)
    assert model.prompts == []
