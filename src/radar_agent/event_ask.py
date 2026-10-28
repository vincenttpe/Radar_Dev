"""事件提問：照護人員打字問一個地點的狀況，在地模型只選題號，答案由事件事實套固定句型。

11 月中甲方 Demo 讓在地模型「有感」的功能（docs/11「11 月中甲方 Demo」）：模型跑在 Jetson 本機，
提問與資料不出場域。流程照 ask.py：模型只回題號 → 先問「您是想問……嗎？」→ 讀者確認後才顯示答案；
對應不到就列出選單讓讀者自己點。模型不寫答案，答案的數字都要通過輸出檢查。
題目與答案句型於 2026-10-19 確認（docs/11）；改這裡屬單向門「給讀者的輸出與用語」（REVIEW.md 第 2 節）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from radar_agent.ask import Match, Question, match_question
from radar_agent.caregiver import SUMMARY_TOPICS, period_head, topic_sentences
from radar_agent.device_events import Summary, read_records, summarize
from radar_agent.local_model import add_model_args, model_from_args
from radar_agent.output_check import check_output
from radar_agent.rewrite import Model

SCHEMA = "event-ask-v0"
# 提示詞（ask.build_prompt）加上 EVENT_QUESTIONS 的版本，記進每次提問的紀錄（prompt_version）。
# 改了 EVENT_QUESTIONS 或 ask.build_prompt 都要升版：v1 是 2026-10-28 longstay 題目加上「久待警報有幾次」
PROMPT_VERSION = "event-prompt-v1"

EVENT_QUESTIONS: list[Question] = [
    Question("falls", "有沒有人跌倒？"),
    Question("calls", "有沒有人主動呼救？"),
    Question("longstay", "有沒有人待太久？久待警報有幾次？"),
    Question("visits", "用了幾次？最久待多久？"),
    Question("offline", "裝置有沒有斷線？"),
    Question("alerts", "有哪些警報？要怎麼處理？"),
]
_BY_ID = {q.id: q for q in EVENT_QUESTIONS}


def answer(question_id: str, summary: Summary, *, place: str) -> str:
    """固定句型的答案，只用 summary 裡的事實：題號就是 caregiver 的事件摘要主題，句子全由 caregiver 決定。
    斷線與無法判讀的紀錄每一題都會寫出來。question_id 不在 EVENT_QUESTIONS 時丟 KeyError。"""
    if question_id not in SUMMARY_TOPICS:
        raise KeyError(question_id)
    if not summary.online:
        return f"{place}：沒有收到資料，無法判斷。"
    lines = topic_sentences(question_id, summary, place)
    return "\n".join([period_head(summary, place, clock=True) + lines[0], *lines[1:]])


def confirm_text(question: Question) -> str:
    return f"您是想問「{question.text}」嗎？"


@dataclass(frozen=True)
class Reply:
    """一次提問的結果。刻意不存讀者打的原文（可能有姓名），只記對到哪一題。"""

    match: Match
    answer: str | None   # 對到題目且答案通過輸出檢查才有；讀者確認後才顯示
    seconds: float       # 模型對應花的時間，不含產生答案
    problems: tuple[str, ...] = ()
    # 答案涵蓋的時間：題目不解析「今天早上」這類時段，畫面要標出答案是哪一段資料（2026-10-19 決定）
    period: tuple[str, str] | None = None

    def data(self) -> dict[str, Any]:
        q = self.match.question
        return {
            "schema": SCHEMA,
            "question": {"id": q.id, "text": q.text} if q else None,
            "confirm": confirm_text(q) if q else None,
            "answer": self.answer,
            "menu": [{"id": m.id, "text": m.text} for m in EVENT_QUESTIONS],
            "model": self.match.model,
            "prompt_version": self.match.prompt_version,
            "seconds": round(self.seconds, 2),
            "from": self.period[0] if self.period else None,
            "to": self.period[1] if self.period else None,
            "rejected": list(self.match.rejected + self.problems),
            "text_layer": "template",
        }


def checked_answer(question: Question, summary: Summary, *, place: str) -> tuple[str | None, tuple[str, ...]]:
    text = answer(question.id, summary, place=place)
    problems = check_output(text, summary, place=place)
    return (None, tuple(problems)) if problems else (text, ())


def _period(summary: Summary) -> tuple[str, str] | None:
    if not summary.online:
        return None
    return f"{summary.online[0].start:%Y-%m-%d %H:%M:%S}", f"{summary.online[-1].end:%Y-%m-%d %H:%M:%S}"


def ask_events(asked: str, summary: Summary, *, place: str, model: Model | None,
               clock: Callable[[], float] = time.perf_counter) -> Reply:
    started = clock()
    match = match_question(asked, EVENT_QUESTIONS, model=model, prompt_version=PROMPT_VERSION)
    seconds = clock() - started
    if match.question is None:
        return Reply(match, None, seconds, period=_period(summary))
    text, problems = checked_answer(match.question, summary, place=place)
    return Reply(match, text, seconds, problems, _period(summary))


def pick(question_id: str, summary: Summary, *, place: str) -> Reply:
    """讀者直接從選單點題目：不經過模型。"""
    q = _BY_ID[question_id]
    text, problems = checked_answer(q, summary, place=place)
    return Reply(Match(q), text, 0.0, problems, _period(summary))


def _text(reply: Reply) -> str:
    q = reply.match.question
    if q is None:
        reasons = "、".join(reply.match.rejected)
        menu = "\n".join(f"{i}. {m.text}" for i, m in enumerate(EVENT_QUESTIONS, start=1))
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
