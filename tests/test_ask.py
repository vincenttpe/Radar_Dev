"""提問對應：讀者打字提問，在地模型只選最接近的固定問題，不自己寫答案。只透過 ask.match_question() 驗證。"""
import re

import pytest
from fakes import FakeModel

from radar_agent.ask import Menu, MenuScope, Question, match_question

MENU = Menu((
    Question("walk", "這週走路的狀況怎麼樣？"),
    Question("how", "步速是怎麼算出來的？"),
    Question("out", "其他問題"),
), "menu-v1")


def test_model_reply_with_a_question_number_picks_that_question():
    model = FakeModel("2")
    match = match_question("速度怎麼算的", MENU, model=model)
    assert match.question == MENU.questions[1]
    assert (match.model, match.rejected) == ("fake-0.1", ())


def test_the_menu_prompt_version_is_recorded_whether_or_not_it_matches():
    assert match_question("速度怎麼算的", MENU, model=FakeModel("2")).prompt_version == "menu-v1"
    match = match_question("速度怎麼算的", MENU, model=FakeModel("x"))
    assert (match.question, match.prompt_version) == (None, "menu-v1")


def test_each_ask_is_matched_on_its_own():
    """同一個模型先回錯格式、再回編號：第一次沒對應到，第二次照編號選題，不受前一次影響。"""
    model = FakeModel("第 2 題吧", "2")
    first, second = (match_question("速度怎麼算的", MENU, model=model) for _ in range(2))
    assert (first.question, first.rejected) == (None, ("模型回答不是單一編號",))
    assert second.question == MENU.questions[1] and len(model.prompts) == 2


@pytest.mark.parametrize("reply", [None, 2, ["2"]])
def test_a_reply_that_is_not_text_counts_as_a_bad_format(reply):
    """模型（或包住它的程式）回的不是文字：當成格式不合，不丟例外（2026-11-02）。"""
    match = match_question("速度怎麼算的", MENU, model=FakeModel(reply))
    assert (match.question, match.rejected, match.why) == (None, ("模型回答不是單一編號",), "no_fit")


def test_zero_means_no_question_fits():
    match = match_question("今天天氣如何", MENU, model=FakeModel("0"))
    assert match.question is None
    assert match.rejected == ("模型判斷沒有符合的問題",)


def test_number_outside_the_menu_is_not_a_match():
    match = match_question("速度怎麼算的", MENU, model=FakeModel("7"))
    assert match.question is None
    assert match.rejected == ("模型回答的編號 7 不在問題清單裡",)


def test_reply_that_is_not_just_a_number_is_not_guessed():
    """模型多寫了說明就不採用，不從句子裡猜編號（小模型常見）。"""
    match = match_question("速度怎麼算的", MENU, model=FakeModel("應該是第 2 題"))
    assert match.question is None
    assert match.rejected == ("模型回答不是單一編號",)


def test_number_with_surrounding_spaces_or_a_period_is_accepted():
    assert match_question("速度怎麼算的", MENU, model=FakeModel(" 2。\n")).question == MENU.questions[1]


def test_model_error_is_not_a_match():
    match = match_question("速度怎麼算的", MENU, model=FakeModel(error=TimeoutError()))
    assert match.question is None
    assert match.rejected == ("模型錯誤：TimeoutError",)


def test_without_a_model_nothing_is_matched():
    match = match_question("速度怎麼算的", MENU, model=None)
    assert (match.question, match.model, match.rejected) == (None, None, ("沒有在地模型",))


def test_blank_or_too_long_question_does_not_call_the_model():
    """算力有限：空白或超過長度上限的提問不送進模型，請讀者從選單點選。"""
    model = FakeModel("1")
    assert match_question("  ", MENU, model=model).rejected == ("沒有輸入提問",)
    assert match_question("走" * 101, MENU, model=model).rejected == ("提問超過 100 字",)
    assert model.prompts == []


def test_prompt_lists_every_question_and_the_asked_text():
    model = FakeModel("1")
    match_question("走路還好嗎", MENU, model=model)
    prompt = model.prompts[0]
    assert all(f"{i}. {q.text}" in prompt for i, q in enumerate(MENU.questions, start=1))
    assert "走路還好嗎" in prompt


def test_asked_text_is_not_kept_in_the_result():
    """提問原文可能有姓名，不留在對應結果裡（結果會進追溯紀錄）。"""
    match = match_question("王小明走路還好嗎", MENU, model=FakeModel("1"))
    assert "王小明" not in repr(match)


# ── 選單範圍（MenuScope，2026-11-04，#76）：題目沒有的問法不要對到主題相同的題目 ──────────
SCOPED = Menu(MENU.questions, "menu-v2", scope=MenuScope(re.compile("為什麼"), "注意事項。\n", "系統範圍說明。"))


def test_a_question_the_rule_refuses_never_reaches_the_model():
    model = FakeModel("1")
    match = match_question("為什麼走不好", SCOPED, model=model)
    assert model.prompts == []
    assert (match.question, match.model, match.why, match.prompt_version) == (None, None, "no_fit", "menu-v2")
    assert match.rejected == ("提問不在固定問題能回答的範圍",)


def test_the_note_goes_before_the_menu_and_the_second_call_checks_the_chosen_question():
    model = FakeModel("2", "1")
    match = match_question("速度怎麼算的", SCOPED, model=model)
    assert match.question == MENU.questions[1]
    assert "注意事項。\n問題清單：" in model.prompts[0]
    assert all(t in model.prompts[1] for t in ("系統範圍說明。", "固定問題：步速是怎麼算出來的？", "使用者的提問：速度怎麼算的"))


@pytest.mark.parametrize("reply", ["0", "2", "是", "1，因為"])
def test_only_an_exact_yes_keeps_the_chosen_question(reply):
    match = match_question("速度怎麼算的", SCOPED, model=FakeModel("2", reply))
    assert (match.question, match.why) == (None, "no_fit")
    assert match.rejected == ("模型確認這一題答不到提問",)


def test_the_check_is_skipped_when_the_model_picked_nothing():
    model = FakeModel("0")
    assert match_question("今天天氣如何", SCOPED, model=model).question is None
    assert len(model.prompts) == 1


def test_a_model_error_during_the_check_counts_as_a_model_error():
    class FailsSecond(FakeModel):
        def __call__(self, prompt: str) -> str:
            if self.prompts:
                raise TimeoutError
            return super().__call__(prompt)

    match = match_question("速度怎麼算的", SCOPED, model=FailsSecond("2"))
    assert (match.question, match.why, match.rejected) == (None, "model_error", ("模型錯誤：TimeoutError",))


def test_a_menu_without_a_scope_still_makes_one_call():
    model = FakeModel("2")
    assert match_question("為什麼走不好", MENU, model=model).question == MENU.questions[1]
    assert len(model.prompts) == 1
