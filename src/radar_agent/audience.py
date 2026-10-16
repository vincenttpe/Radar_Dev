"""讀者權限：哪種 Profile 能看哪類對象的資料。產生文字之前先檢查，不行就不產生。

依據 docs/02「輸出的對象：場域與個人」（2026-10-12 確認）：
- 場域資料（沒有個人 ID，例如 Demo 的廁所）：給居服員與主管機關。
- 個人資料（有 subject_id）：給居服員、醫師、家屬、長者本人；主管機關只看統計，不含個人資料。
沒有 ID 也視同個人資料（REVIEW.md 3.3），所以場域資料也不是誰都能看。

還沒做的：哪一位讀者能看哪幾位長者（讀者怎麼登入、與長者的對應），等會議清單 F4。
改這張表屬單向門「給讀者的輸出與用語」與「個人資料的流向」（REVIEW.md 第 2 節）。
"""
from __future__ import annotations

from typing import Literal

from radar_agent.device_events import Summary
from radar_agent.kg_rules import Assessment

Profile = Literal["居服員", "醫師", "家屬", "長者本人", "主管機關"]
Subject = Literal["場域", "個人"]

ALLOWED: dict[Profile, frozenset[Subject]] = {
    "居服員": frozenset({"場域", "個人"}),
    "醫師": frozenset({"個人"}),
    "家屬": frozenset({"個人"}),
    "長者本人": frozenset({"個人"}),
    "主管機關": frozenset({"場域"}),
}


def subject_of(source: Summary | Assessment) -> Subject:
    """場域事件摘要是場域；知識圖譜等級是某一位長者的量測，算個人。"""
    return "場域" if isinstance(source, Summary) else "個人"


def check_audience(profile: str, source: Summary | Assessment) -> list[str]:
    """回報這個 Profile 不能看這份資料的原因；空清單表示可以。"""
    if profile not in ALLOWED:
        return [f"沒有「{profile}」這種 Profile（只有 {'、'.join(ALLOWED)}）"]
    subject = subject_of(source)
    if subject not in ALLOWED[profile]:
        return [f"{profile}不能看{subject}資料（docs/02「輸出的對象：場域與個人」）"]
    return []
