"""提問對應：讀者打字提問時，在地模型只從固定問題中選最接近的一題，不自己寫答案。

docs/11「知識圖譜 Demo」：在地模型能力有限，回答一律是事先寫好、審核過的固定答案；
模型只負責「選哪一題」，選到後呼叫端要先請讀者確認（「您是想問……嗎？」）再顯示答案。
選不到、模型出錯或回答不合格式，一律當作沒對應到，由呼叫端列出選單讓讀者自己點。
改 prompt 屬單向門「給讀者的輸出與用語」（REVIEW.md 第 2 節）。
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from radar_agent.rewrite import Model

# 知識圖譜提問選單（model_eval.ASK_MENU）的版本：每份選單自己帶版本（Menu.prompt_version）。
# 常數留在 ask：這個版本也涵蓋 ask.build_prompt，改 prompt 的人在同一個檔就看得到要升版
PROMPT_VERSION = "ask-v0"
# 暫定：提問長度上限。太長的提問多半不是在問選單上的問題，也白白吃掉 Jetson 的算力
MAX_ASKED = 100
# 模型只能回一個編號，前後可以有空白或句號；多寫說明就不採用
_REPLY = re.compile(r"(\d+)[.。]?")


@dataclass(frozen=True)
class Question:
    id: str
    text: str


@dataclass(frozen=True)
class MenuScope:
    """選單答得到什麼（2026-11-04，#76）：模型會依主題對題，「跌倒的人有沒有受傷」也對到跌倒，所以分三層把關。

    refuse 規則先擋，不送模型；note 加在給模型的選單前；verify 是對到題目後再問一次模型時的範圍說明。
    known 是人確認過、模型卻對不到的問法（問法, 題目 id）：完全相符就對到題目，不經過模型，提示詞也不用動。
    """

    refuse: re.Pattern[str]
    note: str
    verify: str
    known: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Menu:
    """讀者能問的一份固定問題清單，和它的 prompt 版本（2026-11-02 起題目與版本一起傳，不再分開）。

    題目或 build_prompt 改了都要升 prompt_version，紀錄才追得回模型當時看到什麼；tests 依題目算 prompt 的指紋。
    """

    questions: tuple[Question, ...]
    prompt_version: str
    scope: MenuScope | None = field(default=None, kw_only=True)


@dataclass(frozen=True)
class Match:
    """對應結果。刻意不存讀者打的原文：原文可能有姓名，不進追溯紀錄與 log。"""

    question: Question | None
    model: str | None = None
    prompt_version: str | None = None
    rejected: tuple[str, ...] = ()
    # 對不到的原因代碼，呼叫端依它決定給讀者的提示（2026-11-02）：
    # empty 沒有輸入、too_long 太長、no_model 沒有模型、model_error 模型出錯、no_fit 模型判斷沒有符合或回答不合格式
    why: str | None = None


def build_prompt(asked: str, questions: Sequence[Question], note: str = "") -> str:
    listing = "\n".join(f"{i}. {q.text}" for i, q in enumerate(questions, start=1))
    return (
        "下面是一個長照系統可以回答的問題清單，以及使用者打的提問。\n"
        "請找出清單中意思最接近的一題，只輸出它的編號；都不符合就輸出 0。不要輸出其他文字。\n"
        f"{note}問題清單：\n{listing}\n"
        f"使用者的提問：{asked}"
    )


def build_verify_prompt(asked: str, question: Question, verify: str) -> str:
    return (
        f"{verify}\n"
        f"固定問題：{question.text}\n使用者的提問：{asked}\n"
        "固定問題的答案，是不是就是使用者想知道的事？是就輸出 1，使用者還想知道別的事就輸出 0。不要輸出其他文字。"
    )


def match_question(asked: str, menu: Menu, *, model: Model | None) -> Match:
    """把讀者的提問對應到 menu 其中一題；對應不到時 question 為 None，rejected 寫原因。
    menu.prompt_version 記進紀錄，分得出模型當時看到的是哪一份選單。"""
    questions, prompt_version, scope = menu.questions, menu.prompt_version, menu.scope
    asked = asked.strip()
    if not asked:
        return Match(None, rejected=("沒有輸入提問",), why="empty")
    if len(asked) > MAX_ASKED:
        return Match(None, rejected=(f"提問超過 {MAX_ASKED} 字",), why="too_long")
    if model is None:
        return Match(None, rejected=("沒有在地模型",), why="no_model")

    def miss(reason: str, why: str = "no_fit") -> Match:
        return Match(None, model.name, prompt_version, (reason,), why)

    known_id = dict(scope.known).get(asked.rstrip("?？。. ")) if scope else None
    known = next((q for q in questions if q.id == known_id), None)
    if known:
        return Match(known, None, prompt_version)
    if scope and scope.refuse.search(asked):
        # 沒有呼叫模型，紀錄的 model 留空，追溯時才不會以為模型判斷過
        return Match(None, None, prompt_version, ("提問不在固定問題能回答的範圍",), "no_fit")
    try:
        reply = model(build_prompt(asked, questions, scope.note if scope else ""))
    except Exception as e:  # 模型逾時、當掉都當作沒對應到，讓讀者改從選單點選
        return miss(f"模型錯誤：{type(e).__name__}", "model_error")
    found = _REPLY.fullmatch(reply.strip()) if isinstance(reply, str) else None  # 回的不是文字也當成格式不合
    if not found:
        return miss("模型回答不是單一編號")
    number = int(found.group(1))
    if number == 0:
        return miss("模型判斷沒有符合的問題")
    if number > len(questions):
        return miss(f"模型回答的編號 {number} 不在問題清單裡")
    question = questions[number - 1]
    if scope:
        try:
            check = model(build_verify_prompt(asked, question, scope.verify))
        except Exception as e:
            return miss(f"模型錯誤：{type(e).__name__}", "model_error")
        # 只有明確回 1 才算：回答不合格式時，寧可改列選單
        if not isinstance(check, str) or check.strip().rstrip(".。") != "1":
            return miss("模型確認這一題答不到提問")
    return Match(question, model.name, prompt_version)
