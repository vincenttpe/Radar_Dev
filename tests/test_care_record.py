"""照護紀錄改寫：在地模型把一行一事件的草稿寫成機構格式，不改事實。只透過 care_record() 驗證。

事件用 model_eval 的模擬題組（E2 疑似跌倒、回覆沒事；E6 裝置斷線一段），不含真實資料。
"""
from radar_agent.care_record import care_record
from radar_agent.caregiver import event_notes
from radar_agent.model_eval import PLACE, event_cases

CASES = {c.id: c.source for c in event_cases()}


def draft(case: str) -> str:
    return "\n".join(n.record for n in event_notes(CASES[case], PLACE))


class Scripted:
    name = "scripted"

    def __init__(self, reply: str):
        self.reply = reply
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


def test_without_a_model_the_record_is_the_one_line_per_event_draft():
    out = care_record(CASES["E2"], place=PLACE)
    assert out.layer == "template"
    assert out.text == draft("E2")


GOOD_E2 = "09:06 於1 號廁所，雷達偵測到疑似跌倒，語音詢問 10 秒未獲回應；09:06 長者以語音回覆沒事，警報解除。"


def test_model_rewrite_that_keeps_every_fact_is_adopted():
    model = Scripted(GOOD_E2)
    out = care_record(CASES["E2"], place=PLACE, model=model)
    assert (out.layer, out.text, out.model, out.rejected) == ("model", GOOD_E2, "scripted", ())
    assert out.prompt_version == "record-v0"
    assert draft("E2") in model.prompts[0]


def rejected(case: str, reply: str) -> tuple[str, ...]:
    out = care_record(CASES[case], place=PLACE, model=Scripted(reply))
    assert out.layer == "template" and out.text == draft(case)
    return out.rejected


def test_dropping_an_event_time_falls_back_to_the_draft():
    reasons = rejected("E2", "雷達偵測到疑似跌倒，語音詢問 10 秒未獲回應；長者以語音回覆沒事，警報解除。")
    assert "漏掉草稿裡的數字 6" in reasons


def test_number_not_in_the_facts_falls_back():
    assert "數字 8 不在事實裡" in rejected("E2", GOOD_E2 + "請每 8 小時巡視。")


def test_disconnection_must_still_say_the_situation_is_unknown():
    """斷線期間沒有資料，紀錄不能寫成沒事（REVIEW.md 3.1）。"""
    assert "漏掉「狀況不明」" in rejected("E6", "09:19–09:30 裝置斷線，期間一切正常。")


def test_forbidden_wording_falls_back():
    assert any("誤報" in r for r in rejected("E2", GOOD_E2.replace("警報解除", "判定為誤報")))


def test_more_sentences_than_the_draft_falls_back():
    """模型多加的句子即使數字湊巧對得上也擋下（決策單 D-4 的實例）。"""
    reasons = rejected("E2", "09:06 雷達偵測到疑似跌倒，語音詢問 10 秒未獲回應。09:06 長者語音回覆沒事，警報解除。建議 10 分鐘後再去看看。")
    assert "句數 3 多於草稿的 2 句" in reasons


def test_too_long_falls_back():
    assert any(r.startswith("長度") for r in rejected("E2", GOOD_E2 + "，" * 200))


class Broken:
    name = "broken"

    def __call__(self, prompt: str) -> str:
        raise TimeoutError


def test_model_error_falls_back():
    out = care_record(CASES["E2"], place=PLACE, model=Broken())
    assert (out.layer, out.rejected) == ("template", ("模型錯誤：TimeoutError",))


def test_no_events_means_no_record_and_no_model_call():
    model = Scripted("不該被用到")
    out = care_record(CASES["E9"], place=PLACE, model=model)
    assert (out.text, out.layer) == ("", "template")
    assert model.prompts == []


def test_institution_sample_is_given_to_the_model_as_a_style_example():
    model = Scripted(GOOD_E2)
    care_record(CASES["E2"], place=PLACE, model=model, style="【時間】事件經過。處置：……")
    assert "【時間】事件經過。處置：……" in model.prompts[0]


class Echo:
    name = "echo"

    def __call__(self, prompt: str) -> str:
        return prompt.rsplit("原文：\n", 1)[-1]


def test_copying_the_draft_is_adopted_for_every_simulated_case():
    """草稿本身要通過檢查；否則模型怎麼寫都會被退回。"""
    for case, summary in CASES.items():
        out = care_record(summary, place=PLACE, model=Echo())
        assert out.rejected == (), (case, out.rejected)


def test_empty_model_output_falls_back():
    assert rejected("E2", "   ") == ("模型沒有輸出",)
