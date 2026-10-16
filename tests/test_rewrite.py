"""在地模型改寫層（docs/02「在地模型負載與分流」）：模型的輸出通過檢查才採用，否則退回範本。

模型以「prompt → 文字」的假模型代替（外部服務的邊界，REVIEW.md 3.5）；範本、檢查都用真的。
"""
import pytest
from test_caregiver import timeline

from radar_agent import kg_rules
from radar_agent.caregiver import caregiver_text
from radar_agent.device_events import summarize
from radar_agent.rewrite import PROMPT_VERSION, rewrite

PLACE = "1 號廁所"
ALL_ONLINE = summarize(timeline("09:00:00", "09:30:00", [("09:05:00", "09:13:00"), ("09:20:00", "09:21:30")]))
# 範本：1 號廁所 10/14：有人使用 2 次，最長一次 8 分鐘。跌倒警報 0 次。久待警報 0 次。這段時間裝置都在線。
GOOD = "1 號廁所在 10/14 有人使用 2 次，最久一次待了 8 分鐘；跌倒警報 0 次、久待警報 0 次，裝置一直在線。"


class FakeModel:
    """回固定文字，或丟出指定的例外；記下收到的 prompt。"""

    name = "fake-0.1"

    def __init__(self, reply: str = "", error: Exception | None = None):
        self.reply, self.error, self.prompts = reply, error, []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return self.reply


def test_without_a_model_the_template_is_used():
    result = rewrite(ALL_ONLINE, place=PLACE)
    assert (result.text, result.layer, result.model, result.rejected) == (
        caregiver_text(ALL_ONLINE, place=PLACE), "template", None, ()
    )


def test_a_faithful_rewrite_is_used_and_traced_to_model_and_prompt_version():
    result = rewrite(ALL_ONLINE, place=PLACE, model=FakeModel(GOOD))
    assert (result.text, result.layer, result.model, result.prompt_version, result.rejected) == (
        GOOD, "model", "fake-0.1", PROMPT_VERSION, ()
    )


def test_the_prompt_carries_the_template_and_nothing_else_from_the_data():
    model = FakeModel(GOOD)
    rewrite(ALL_ONLINE, place=PLACE, model=model)
    (prompt,) = model.prompts
    template = caregiver_text(ALL_ONLINE, place=PLACE)
    assert prompt.endswith("原文：\n" + template)


def _fallback(source, reply, place=PLACE):
    result = rewrite(source, place=place, model=FakeModel(reply))
    assert result.layer == "template"
    assert result.text == caregiver_text(source, place=place)
    assert result.model == "fake-0.1"
    return result.rejected


def test_an_invented_number_falls_back_to_the_template():
    assert "數字 3 不在事實裡" in _fallback(ALL_ONLINE, GOOD.replace("使用 2 次", "使用 2 次（其中 3 人）"))


def test_dropping_the_disconnection_caveat_falls_back():
    """模型把斷線那句刪掉，讀者會以為整段時間都沒事（REVIEW.md 3.1）。"""
    records = timeline("09:00:00", "09:10:00", [("09:02:00", "09:03:00")]) + timeline("09:40:00", "09:50:00")
    summary = summarize(records)
    reply = "1 號廁所 10/14 有人使用 1 次，最長一次 1 分鐘。在線期間跌倒警報 0 次，在線期間久待警報 0 次。"
    rejected = _fallback(summary, reply)
    assert "漏掉「無法判斷」" in rejected
    assert "漏掉範本裡的數字 40" in rejected


def test_leading_zeros_in_times_are_not_counted_as_dropped_numbers():
    records = timeline("09:00:00", "09:10:00", [("09:02:00", "09:03:00")]) + timeline("09:40:00", "09:50:00")
    reply = (
        "1 號廁所 10/14 有人使用 1 次，最長一次 1 分鐘。在線期間跌倒警報 0 次，在線期間久待警報 0 次。"
        "9:10 到 9:40 裝置斷線，這段時間無法判斷是否有跌倒。"
    )
    assert rewrite(summarize(records), place=PLACE, model=FakeModel(reply)).layer == "model"


def test_medical_wording_falls_back():
    assert "不能出現「治療」（非醫療器材）" in _fallback(ALL_ONLINE, GOOD + "不需要治療。")


def test_overlong_output_falls_back():
    rejected = _fallback(ALL_ONLINE, GOOD * 3)
    assert any(r.startswith("長度") for r in rejected)


def test_model_errors_and_empty_output_fall_back():
    for model, reason in [
        (FakeModel(error=TimeoutError()), "模型錯誤：TimeoutError"),
        (FakeModel("   \n"), "模型沒有輸出"),
    ]:
        result = rewrite(ALL_ONLINE, place=PLACE, model=model)
        assert (result.layer, result.rejected) == ("template", (reason,))


def test_kg_suggestion_must_be_copied_verbatim():
    assessment = kg_rules.assess(kg_rules.load(), "來回行走", "5.4")
    template = caregiver_text(assessment)
    verbatim = "你的來回行走 5.4 秒（2.44 公尺），是「很好」的 4 分。\n" + template.split("\n", 1)[1]
    assert rewrite(assessment, model=FakeModel(verbatim)).layer == "model"

    paraphrased = verbatim.replace("持續規律步行", "繼續規律走路")
    assert "知識圖譜原文（suggestion）沒有照抄" in _fallback(assessment, paraphrased, place="")


def test_unapproved_kg_notice_must_be_kept():
    assessment = kg_rules.assess(kg_rules.load(), "來回行走", "5.45")
    reply = "來回行走 5.45 秒（2.44 公尺）：目前無法評估，已交由審核人確認。"
    assert "漏掉「僅供測試」" in _fallback(assessment, reply, place="")


def test_a_template_that_fails_its_own_check_is_not_handed_to_the_model():
    model = FakeModel(GOOD)
    with pytest.raises(ValueError, match="範本沒通過輸出檢查"):
        rewrite(ALL_ONLINE, place="治療室", model=model)
    assert model.prompts == []
