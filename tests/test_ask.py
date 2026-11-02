"""提問對應：讀者打字提問，在地模型只選最接近的固定問題，不自己寫答案。只透過 ask.match_question() 驗證。"""
from fakes import FakeModel

import hashlib

from radar_agent.ask import Menu, Question, build_prompt, match_question

MENU = Menu((
    Question("walk", "這週走路的狀況怎麼樣？"),
    Question("how", "步速是怎麼算出來的？"),
    Question("out", "其他問題"),
), "menu-v1")


def menu_digest(menu: Menu) -> str:
    """一份選單的 prompt 指紋：題目套進 build_prompt 的文字（2026-11-02 起每份選單都用這個算，不另外手寫）。"""
    return hashlib.sha256(build_prompt("{提問}", menu.questions).encode("utf-8")).hexdigest()[:16]


def test_model_reply_with_a_question_number_picks_that_question():
    model = FakeModel("2")
    match = match_question("速度怎麼算的", MENU, model=model)
    assert match.question == MENU.questions[1]
    assert (match.model, match.rejected) == ("fake-0.1", ())


def test_the_menu_prompt_version_is_recorded_whether_or_not_it_matches():
    assert match_question("速度怎麼算的", MENU, model=FakeModel("2")).prompt_version == "menu-v1"
    match = match_question("速度怎麼算的", MENU, model=FakeModel("x"))
    assert (match.question, match.prompt_version) == (None, "menu-v1")


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
