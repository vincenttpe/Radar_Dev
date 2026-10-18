"""照護紀錄改寫：在地模型把一行一事件的照護紀錄草稿，寫成機構習慣的紀錄格式。

docs/adr/0002「AI 的分工」的「撰寫」：照護紀錄是查核點 B4 與 LLM 文字指標的驗收來源，由 AI 產生。
事實仍由 event_notes() 決定（原則 2）；模型只改寫說法，輸出沒通過檢查就退回一行一事件的草稿。
機構格式的樣本（會議清單 F13）由呼叫端傳入，只當寫法示範；樣本必須先去識別。
改 prompt 或檢查屬單向門「給讀者的輸出與用語」（REVIEW.md 第 2 節）。
"""
from __future__ import annotations

import re
from decimal import Decimal

from radar_agent.caregiver import event_notes
from radar_agent.device_events import Summary
from radar_agent.output_check import check_output
from radar_agent.rewrite import Model, Rewritten

PROMPT_VERSION = "record-v0"
# 草稿裡有這些字，改寫後一定要留著：斷線期間沒有資料、沒有回應，不能被寫成沒事（REVIEW.md 3.1）
REQUIRED_PHRASES = ("狀況不明", "未獲回應")
# 暫定：改寫最多是草稿的幾倍長
MAX_LENGTH_RATIO = 2
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def build_prompt(draft: str, style: str | None) -> str:
    """只放草稿與（去識別的）機構格式樣本，不放任何草稿以外的事實。"""
    sample = f"機構的紀錄格式範例（只參考寫法，不要抄裡面的內容）：\n{style}\n" if style else ""
    return (
        "你是長照機構的照護紀錄助手。請把下面一行一事件的紀錄草稿，寫成通順的照護紀錄。\n"
        "規則：\n"
        "1. 只改說法，不新增、不刪除任何事件或事實；不要加上建議或推測。\n"
        "2. 時間、數字照原樣寫，不換算。\n"
        f"3. 原文有「{'」「'.join(REQUIRED_PHRASES)}」的地方一定要保留。\n"
        "4. 不使用「診斷」「治療」「處方」「保證」「誤報」等字眼。\n"
        f"5. 句數不多於原文，長度不超過原文的 {MAX_LENGTH_RATIO} 倍。只輸出紀錄，不加說明。\n"
        f"{sample}"
        "原文：\n"
        f"{draft}"
    )


def _numbers(text: str) -> set[Decimal]:
    return {Decimal(n) for n in _NUMBER.findall(text)}


def _sentences(text: str) -> int:
    return len([s for s in re.split(r"[。！？\n]", text) if s.strip()])


def _kept(draft: str, text: str) -> list[str]:
    """check_output 擋「多出來的」；這裡擋「被拿掉的」與「多加的句子」。"""
    problems = [f"漏掉草稿裡的數字 {n}" for n in sorted(_numbers(draft) - _numbers(text))]
    problems += [f"漏掉「{p}」" for p in REQUIRED_PHRASES if p in draft and p not in text]
    if _sentences(text) > _sentences(draft):
        problems.append(f"句數 {_sentences(text)} 多於草稿的 {_sentences(draft)} 句")
    if len(text) > MAX_LENGTH_RATIO * len(draft):
        problems.append(f"長度 {len(text)} 字超過草稿的 {MAX_LENGTH_RATIO} 倍")
    return problems


def care_record(
    summary: Summary, *, place: str, model: Model | None = None, style: str | None = None
) -> Rewritten:
    """居服員的照護紀錄。沒有事件時是空字串；沒有模型、模型出錯或輸出沒通過檢查時，用一行一事件的草稿。"""
    draft = "\n".join(n.record for n in event_notes(summary, place))
    if model is None or not draft:
        return Rewritten(draft, "template", "居服員")

    def fallback(*reasons: str) -> Rewritten:
        return Rewritten(draft, "template", "居服員", model.name, PROMPT_VERSION, reasons)

    try:
        text = model(build_prompt(draft, style)).strip()
    except Exception as e:  # 模型逾時、當掉都退回草稿，不讓紀錄停住
        return fallback(f"模型錯誤：{type(e).__name__}")
    if not text:
        return fallback("模型沒有輸出")
    problems = check_output(text, summary, place=place) + _kept(draft, text)
    if problems:
        return fallback(*problems)
    return Rewritten(text, "model", "居服員", model.name, PROMPT_VERSION)
