"""事件提問：照護人員打字問一個地點的狀況，在地模型只選題號，答案由事件事實套固定句型。

11 月中甲方 Demo 讓在地模型「有感」的功能（docs/11「11 月中甲方 Demo」）：模型跑在 Jetson 本機，
提問與資料不出場域。流程照 ask.py：模型只回題號 → 先問「您是想問……嗎？」→ 讀者確認後才顯示答案；
對應不到就列出選單讓讀者自己點。模型不寫答案，答案的數字都要通過輸出檢查。
題目與答案句型於 2026-10-19 確認（docs/11）；改這裡屬單向門「給讀者的輸出與用語」（REVIEW.md 第 2 節）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from radar_agent.ask import MAX_ASKED, Match, Menu, MenuScope, Question, match_question
from radar_agent.caregiver import SUMMARY_TOPICS, no_data, period_head, topic_sentences
from radar_agent.device_events import Summary, read_records, summarize
from radar_agent.local_model import add_model_args, model_from_args
from radar_agent.output_check import check_output
from radar_agent.rewrite import Model, rewrite_handover

SCHEMA = "event-ask-v0"
# 提示詞（ask.build_prompt）加上 EVENT_QUESTIONS 的版本，記進每次提問的紀錄（prompt_version）。
# 改了 EVENT_QUESTIONS、EVENT_SCOPE 或 ask.build_prompt 都要升版：v1 是 2026-10-28 longstay 題目加上「久待警報有幾次」；
# v2 是 2026-11-04 加上 EVENT_SCOPE（#76）；v3 是同日加上 EVENT_SCOPE.known
PROMPT_VERSION = "event-prompt-v3"

EVENT_QUESTIONS: tuple[Question, ...] = (
    Question("falls", "有沒有人跌倒？"),
    Question("calls", "有沒有人主動呼救？"),
    Question("longstay", "有沒有人待太久？久待警報有幾次？"),
    Question("visits", "用了幾次？最久待多久？"),
    Question("offline", "裝置有沒有斷線？"),
    Question("alerts", "有哪些警報？要怎麼處理？"),
)
_BY_ID = {q.id: q for q in EVENT_QUESTIONS}
# 選單答不到的問法（2026-11-04，#76；radar-model-eval --scope-questions 量）。規則只擋字面明確的，其餘靠 note 與 verify。
# 選單沒有長者回應的題目，所以提到回應的一律不送模型；誤擋時讀者仍可從選單點
EVENT_SCOPE = MenuScope(
    re.compile(
        r"為什麼|為何|怎麼會|原因"  # 問原因
        r"|代表|意思|可信|可靠|安全嗎|安全了|危險|嚴重|要緊"  # 問解讀或判斷
        r"|要不要|該不該|需不需要|不用去|不必去"
        r"|沒事|回答|回覆|回應|沒回|不回"  # 長者回應
        r"|通知|傳給|傳送|寄給|轉給|告訴|聯絡|打給|打電話|關掉|設定|調整"  # 要系統代辦或改設定
        r"|變多|變少|增加|減少|趨勢|比上|比昨|比前|比較多|比較少"  # 比較與趨勢
        r"|會不會|預計|預測"  # 預測
        r"|受傷|骨折|血壓|失智|生病|吃藥|看醫生|送醫|救護車"  # 身體與醫療
        r"|.{21,}"  # 長句：先講一件事再追問，模型會依前半句對題；題組裡有答案的問法幾乎都在 20 字內
    ),
    "注意：提到相同主題不代表符合。只有提問要的就是該題的答案才算符合。"
    "問原因、身分（誰、哪一位）、身體狀況、預測未來、比較或趨勢、判斷該怎麼做或代表什麼，或要系統代辦事情，都輸出 0。\n"
    "例：「跌倒的人有沒有受傷」輸出 0；「誰跌倒了」輸出 0；「明天會不會斷線」輸出 0；「幫我通知家屬」輸出 0；「這個月跌倒有變多嗎」輸出 0；「今天有人摔倒嗎」輸出跌倒那一題的編號。\n",
    "系統只能回答下面這個固定問題，答案只有事件有沒有發生、次數、時間與處理步驟，不知道是誰、原因、身體狀況，不能預測、比較趨勢，也不能代辦事情。",
    # 2026-11-04 審查決定要對得到（PR #79）；加了範圍限制後模型都回 0。
    # 「誰卡在廁所很久」是刻意的例外：其他問「誰」的問法仍回 0，這一句的答案是久待次數，不會說是誰
    known=(("現在我要去處理什麼", "alerts"), ("上了幾次廁所", "visits"), ("誰卡在廁所很久", "longstay")),
)
# 沒有答案時給讀者的提示，依原因不同（2026-11-02 決定由本 repo 產生，Dashboard 照原文顯示，不自己寫句子）。
# 鍵是 ask.Match.why；unavailable 是對到題目、但答案沒通過輸出檢查
NOTICES = {
    "no_fit": "沒有對應到固定問題。請換個說法再問一次，或從選單選一題。",
    "no_model": "目前無法用打字提問，請從選單選一題。",
    "model_error": "目前無法用打字提問，請從選單選一題。",
    "too_long": f"提問太長（超過 {MAX_ASKED} 字），請縮短後再問，或從選單選一題。",
    "empty": "請輸入提問，或從選單選一題。",
    "unavailable": "這一題的答案暫時無法顯示，請通知資訊人員。",
}


def answer(question_id: str, summary: Summary, *, place: str) -> str:
    """固定句型的答案，只用 summary 裡的事實：題號就是 caregiver 的事件摘要主題，句子全由 caregiver 決定。
    斷線與無法判讀的紀錄每一題都會寫出來。question_id 不在 EVENT_QUESTIONS 時丟 KeyError。"""
    if question_id not in SUMMARY_TOPICS:
        raise KeyError(question_id)
    if not summary.online:
        return f"{place}：{no_data(summary)}。"
    lines = topic_sentences(question_id, summary, place)
    return "\n".join([period_head(summary, place, clock=True) + lines[0], *lines[1:]])


def confirm_text(question: Question) -> str:
    return f"您是想問「{question.text}」嗎？"


# 一題的答案：(文字, 沒通過的原因)。文字是 None 時不顯示答案，原因寫在 rejected。
# 選單的答案函式一律照位置傳 (題目, 摘要, 地點)，所以 checked_answer 的 place 不像 answer() 那樣限關鍵字
Answer = Callable[[Question, Summary, str], tuple[str | None, tuple[str, ...]]]


@dataclass(frozen=True)
class EventMenu(Menu):
    """事件提問的一份選單：題目與 prompt 版本（Menu），加上輸出格式名稱與「答案從哪來」（2026-11-02）。"""

    schema: str
    answer: Answer


def checked_answer(question: Question, summary: Summary, place: str) -> tuple[str | None, tuple[str, ...]]:
    text = answer(question.id, summary, place=place)
    problems = check_output(text, summary, place=place)
    return (None, tuple(problems)) if problems else (text, ())


EVENT_MENU = EventMenu(EVENT_QUESTIONS, PROMPT_VERSION, SCHEMA, checked_answer, scope=EVENT_SCOPE)


# ── Jetson 內部 Demo 的 7 題選單（docs/21）：多一題「交班重點」────────────
# 只在 Jetson 內部 Demo（2026-10-30 決定）；radar-ask 與 Dashboard 仍是上面的 6 題。2026-11-02 從 demo/jetson_serve.py
# 搬來，model_eval 才能用同一份選單評估（--demo-questions）。「幫我寫一份交班紀錄」也對到這一題，答案開頭註明不是正式紀錄。
# 題目、EVENT_SCOPE 或 ask.build_prompt 改了要升 DEMO_PROMPT_VERSION（tests/test_jetson_serve.py 會檢查）；v1 加上 EVENT_SCOPE，v2 加上 EVENT_SCOPE.known
# 題目文字就是確認句裡的「您是想問『幫我整理交班重點』嗎？」。只寫「交班重點」實測有 2 題對錯題（2026-10-30）
HANDOVER_QUESTION = Question("handover", "幫我整理交班重點")
HANDOVER_NOTE = "以下是交班重點，不是機構的正式交班紀錄。"
DEMO_PROMPT_VERSION = "jetson-demo-ask-v2"
DEMO_SCHEMA = "jetson-demo-ask-v0"  # 選單 7 題、多了 handover，和 Dashboard 用的 event-ask-v0 不同


def _demo_answer(question: Question, summary: Summary, place: str) -> tuple[str | None, tuple[str, ...]]:
    """前 6 題和 radar-ask 相同；交班重點回原句（沒經過模型），頁面再另外拿模型改寫的版本並排比對。
    原句沒通過輸出檢查時不交出文字、寫出原因，和前 6 題一致。"""
    if question.id != HANDOVER_QUESTION.id:
        return checked_answer(question, summary, place)
    try:
        return f"{HANDOVER_NOTE}\n{rewrite_handover(summary, place=place).text}", ()
    except ValueError as e:
        return None, (str(e),)


DEMO_MENU = EventMenu((*EVENT_QUESTIONS, HANDOVER_QUESTION), DEMO_PROMPT_VERSION, DEMO_SCHEMA, _demo_answer, scope=EVENT_SCOPE)


@dataclass(frozen=True)
class Reply:
    """一次提問的結果。刻意不存讀者打的原文（可能有姓名），只記對到哪一題。"""

    match: Match
    answer: str | None   # 對到題目且答案通過輸出檢查才有；讀者確認後才顯示
    seconds: float       # 模型對應花的時間，不含產生答案
    problems: tuple[str, ...] = ()
    # 答案涵蓋的時間：題目不解析「今天早上」這類時段，畫面要標出答案是哪一段資料（2026-10-19 決定）
    period: tuple[str, str] | None = None
    menu: EventMenu = EVENT_MENU  # 預設是 radar-ask 與 Dashboard 的 6 題

    def notice(self) -> str | None:
        """沒有答案時一定有一句提示；有答案時是 None。"""
        if self.answer is not None:
            return None
        if self.match.question is not None:
            return NOTICES["unavailable"]
        return NOTICES[self.match.why or "no_fit"]

    def data(self) -> dict[str, Any]:
        q, menu = self.match.question, self.menu
        return {
            "schema": menu.schema,
            "question": {"id": q.id, "text": q.text} if q else None,
            "confirm": confirm_text(q) if q else None,
            "answer": self.answer,
            "notice": self.notice(),
            "menu": [{"id": m.id, "text": m.text} for m in menu.questions],
            "model": self.match.model,
            "prompt_version": self.match.prompt_version,
            "seconds": round(self.seconds, 2),
            "from": self.period[0] if self.period else None,
            "to": self.period[1] if self.period else None,
            "rejected": list(self.match.rejected + self.problems),
            "text_layer": "template",
        }


def answer_period(summary: Summary) -> tuple[str, str] | None:
    """答案涵蓋的時間（第一筆在線到最後一筆在線）；沒有收到資料時是 None。Jetson 內部 Demo 也用。"""
    if not summary.online:
        return None
    return f"{summary.online[0].start:%Y-%m-%d %H:%M:%S}", f"{summary.online[-1].end:%Y-%m-%d %H:%M:%S}"


def ask_events(asked: str, summary: Summary, *, place: str, model: Model | None, menu: EventMenu = EVENT_MENU,
               clock: Callable[[], float] = time.perf_counter) -> Reply:
    """模型從 menu 選一題，答案由 menu.answer 產生。radar-ask 用 6 題的 EVENT_MENU，Jetson 內部 Demo 用 DEMO_MENU。"""
    started = clock()
    match = match_question(asked, menu, model=model)
    seconds = clock() - started
    if match.question is None:
        return Reply(match, None, seconds, period=answer_period(summary), menu=menu)
    text, problems = menu.answer(match.question, summary, place)
    return Reply(match, text, seconds, problems, answer_period(summary), menu)


def pick(question_id: str, summary: Summary, *, place: str) -> Reply:
    """讀者直接從選單點題目：不經過模型。"""
    q = _BY_ID[question_id]
    text, problems = checked_answer(q, summary, place)
    return Reply(Match(q), text, 0.0, problems, answer_period(summary), EVENT_MENU)


def _text(reply: Reply) -> str:
    q = reply.match.question
    if q is None:
        reasons = "、".join(reply.match.rejected)
        menu = "\n".join(f"{i}. {m.text}" for i, m in enumerate(reply.menu.questions, start=1))
        return f"沒有對應到固定問題（{reasons}）。請從選單選一題：\n{menu}"
    head = confirm_text(q)
    if reply.match.model:
        head += f"（{reply.match.model}，{reply.seconds:.2f} 秒）"
    if reply.answer is None:
        return f"{head}\n答案沒通過輸出檢查，不顯示：{'、'.join(reply.problems)}"
    return f"{head}\n{reply.answer}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="事件提問：在地模型把提問對到固定問題，答案由事件事實套固定句型")
    ap.add_argument("events", type=Path, help="紀錄檔：雲端格式 {timestamp, topic, resolution, payload}，JSON 陣列或一行一筆")
    ap.add_argument("--place", required=True, help="地點名稱，例如「1 號廁所」；不放長者姓名等個人資料")
    what = ap.add_mutually_exclusive_group(required=True)
    what.add_argument("--question", help="讀者打的提問（要搭配模型）")
    what.add_argument("--pick", choices=list(_BY_ID), help="讀者直接從選單點的題目，不經過模型")
    ap.add_argument("--json", action="store_true", help="改輸出 JSON 給 Dashboard（docs/16_Dashboard對接說明.md）")
    add_model_args(ap, required=False)
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    if not args.place.strip():
        print("錯誤：--place 不能是空白", file=sys.stderr)
        return 2
    try:
        model = model_from_args(args)
        summary = summarize(read_records(args.events))
        if args.pick:
            reply = pick(args.pick, summary, place=args.place)
        else:
            reply = ask_events(args.question, summary, place=args.place, model=model)
    except (OSError, ValueError) as e:  # 讀檔失敗、模型不能用，或處置提示對不上 NOTE_KINDS
        print(f"錯誤：{e}", file=sys.stderr)
        return 2
    print(json.dumps(reply.data(), ensure_ascii=False, indent=2) if args.json else _text(reply))
    return 1 if reply.problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
