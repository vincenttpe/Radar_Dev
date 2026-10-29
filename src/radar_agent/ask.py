"""提問對應：讀者打字提問時，在地模型只從固定問題中選最接近的一題，不自己寫答案。

docs/11「知識圖譜 Demo」：在地模型能力有限，回答一律是事先寫好、審核過的固定答案；
模型只負責「選哪一題」，選到後呼叫端要先請讀者確認（「您是想問……嗎？」）再顯示答案。
選不到、模型出錯或回答不合格式，一律當作沒對應到，由呼叫端列出選單讓讀者自己點。
改 prompt 屬單向門「給讀者的輸出與用語」（REVIEW.md 第 2 節）。
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from radar_agent.rewrite import Model

# 提示詞（build_prompt）加上知識圖譜提問選單（目前是 model_eval.ASK_MENU）的版本；選單或 build_prompt 改了都要升版，紀錄才追得回模型當時看到什麼
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
class Match:
    """對應結果。刻意不存讀者打的原文：原文可能有姓名，不進追溯紀錄與 log。"""

    question: Question | None
    model: str | None = None
    prompt_version: str | None = None
    rejected: tuple[str, ...] = ()


def build_prompt(asked: str, questions: Sequence[Question]) -> str:
    listing = "\n".join(f"{i}. {q.text}" for i, q in enumerate(questions, start=1))
    return (
        "下面是一個長照系統可以回答的問題清單，以及使用者打的提問。\n"
        "請找出清單中意思最接近的一題，只輸出它的編號；都不符合就輸出 0。不要輸出其他文字。\n"
        f"問題清單：\n{listing}\n"
        f"使用者的提問：{asked}"
    )


def match_question(asked: str, questions: Sequence[Question], *, model: Model | None,
                   prompt_version: str = PROMPT_VERSION) -> Match:
    """把讀者的提問對應到 questions 其中一題；對應不到時 question 為 None，rejected 寫原因。
    prompt_version 記進紀錄：用自己選單的呼叫端（例如事件提問）要傳自己的版本。"""
    asked = asked.strip()
    if not asked:
        return Match(None, rejected=("沒有輸入提問",))
    if len(asked) > MAX_ASKED:
        return Match(None, rejected=(f"提問超過 {MAX_ASKED} 字",))
    if model is None:
        return Match(None, rejected=("沒有在地模型",))

    def miss(reason: str) -> Match:
        return Match(None, model.name, prompt_version, (reason,))

    try:
        reply = model(build_prompt(asked, questions))
    except Exception as e:  # 模型逾時、當掉都當作沒對應到，讓讀者改從選單點選
        return miss(f"模型錯誤：{type(e).__name__}")
    found = _REPLY.fullmatch(reply.strip())
    if not found:
        return miss("模型回答不是單一編號")
    number = int(found.group(1))
    if number == 0:
        return miss("模型判斷沒有符合的問題")
    if number > len(questions):
        return miss(f"模型回答的編號 {number} 不在問題清單裡")
    return Match(questions[number - 1], model.name, prompt_version)
