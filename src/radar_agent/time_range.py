"""依時間範圍挑出紀錄：Dashboard 依時間範圍要資料，本 repo 只整理這段時間（docs/16「第一階段的流程」）。

時間和紀錄的 timestamp 一樣沒有時區，照原樣比較（docs/05 A4、A49）。範圍含開始、不含結束，
相鄰兩段（例如 08:00–10:00 與 10:00–12:00）不會重複算同一筆。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from radar_agent.device_events import TIMESTAMP_FORMAT

BOUND_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")


def parse_bound(text: str) -> datetime:
    """「YYYY-MM-DD HH:MM」或「YYYY-MM-DD HH:MM:SS」；其他寫法拒收，不猜。"""
    for fmt in BOUND_FORMATS:
        try:
            return datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
    raise ValueError(f"時間「{text}」看不懂，請寫成 YYYY-MM-DD HH:MM 或 YYYY-MM-DD HH:MM:SS")


def in_range(records: list[dict[str, Any]], start: datetime | None, end: datetime | None) -> list[dict[str, Any]]:
    """留下 start <= timestamp < end 的紀錄；沒給的那一端不限。

    時間讀不懂的紀錄分不出在不在範圍內，照樣留下，交給 summarize 列為無法判讀（不悄悄略過，REVIEW.md 3.1）。
    """
    if start is not None and end is not None and start >= end:
        raise ValueError(f"開始時間 {start:%Y-%m-%d %H:%M:%S} 要早於結束時間 {end:%Y-%m-%d %H:%M:%S}")
    kept = []
    for record in records:
        try:
            when = datetime.strptime(str(record["timestamp"]), TIMESTAMP_FORMAT)
        except (KeyError, TypeError, ValueError):
            kept.append(record)
            continue
        if (start is None or when >= start) and (end is None or when < end):
            kept.append(record)
    return kept
