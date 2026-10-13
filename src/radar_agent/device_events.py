"""整理裝置事件：把雲端外殼格式的紀錄整理成造訪等摘要（docs/adr/0001）。

輸入是 {"timestamp", "topic", "resolution", "payload"} 的紀錄清單（docs/05 A2、A10）。
用語見 CONTEXT.md「雷達資料」。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
PRESENT_EVENTS = {"presence", "moving", "static"}
# 雷達負責人書面回覆的 10 種 event（docs/05 A3）
KNOWN_EVENTS = PRESENT_EVENTS | {"online", "offline", "absence", "longstay", "fall", "resolved", "error"}
# 超過這麼久沒有任何紀錄就算斷線。暫定值：實測心跳約每 4–6 秒一筆（docs/05 A10、D3）。
HEARTBEAT_GAP = timedelta(seconds=30)


@dataclass(frozen=True)
class Period:
    start: datetime
    end: datetime

    @property
    def duration(self) -> timedelta:
        return self.end - self.start


@dataclass(frozen=True)
class FallAlert:
    time: datetime
    # event=fall 且 mode=speech：語音偵測到主動呼救，不一定真的跌倒（docs/05 A3）
    distress_call: bool


@dataclass(frozen=True)
class SettingChange:
    """裝置回報的偵測設定（例如 fallSwitch、stillThreshold）；意義待 docs/05 A31。"""
    time: datetime
    values: dict[str, Any]


@dataclass
class Summary:
    visits: list[Period] = field(default_factory=list)
    online: list[Period] = field(default_factory=list)
    # 造訪途中斷線：不知道人何時離開，只記到斷線前最後一筆，不算停留時間。
    interrupted_visits: list[Period] = field(default_factory=list)
    # 資料結束時人還在：記到最後一筆；可能正是久待未出，不能丟掉
    ongoing_visit: Period | None = None
    duplicates_removed: int = 0
    # 跌倒警報：解除（resolved）或無人（absence）之前重複送出的 fall 算同一次（fallRepeat，docs/05 A26）
    fall_alerts: list[FallAlert] = field(default_factory=list)
    # 語音回覆「沒事」：resolved + noNeedHelp（docs/05 A30）；不直接當誤報（C2）
    no_need_help_replies: list[datetime] = field(default_factory=list)
    # 久待：滯留超過 stillThreshold 時裝置送出 longstay（docs/05 A26）
    longstay_alerts: list[datetime] = field(default_factory=list)
    setting_changes: list[SettingChange] = field(default_factory=list)
    errors: list[tuple[datetime, str]] = field(default_factory=list)
    # 時間或格式讀不懂的紀錄：原樣保留、不參與任何計算
    unreadable: list[dict[str, Any]] = field(default_factory=list)
    # 規格外的 event：仍算心跳，但要請人確認
    unknown_events: list[dict[str, Any]] = field(default_factory=list)


def summarize(records: list[dict[str, Any]]) -> Summary:
    """整理一台裝置的紀錄（不同 topic 混在一起會丟出 ValueError）。"""
    summary = Summary()
    parsed = _parse(records, summary)
    start: datetime | None = None
    online_start: datetime | None = None
    last: datetime | None = None
    fall_active = False
    for when, payload, record in parsed:
        event = payload.get("event")
        if event == "offline":
            if online_start is not None:
                summary.online.append(Period(online_start, when))
            if start is not None and last is not None:
                summary.interrupted_visits.append(Period(start, last))
            start = online_start = last = None
            continue
        if last is None or when - last > HEARTBEAT_GAP:
            if online_start is not None and last is not None:
                summary.online.append(Period(online_start, last))
            if start is not None and last is not None:
                summary.interrupted_visits.append(Period(start, last))
                start = None
            online_start = when
        last = when
        if event is not None and event not in KNOWN_EVENTS:
            summary.unknown_events.append(record)
        if event == "error":
            summary.errors.append((when, str(payload.get("message", ""))))
        if "params" in payload:
            summary.setting_changes.append(SettingChange(when, payload["params"].get("sys.config", {})))
        if event == "fall":
            if not fall_active:
                summary.fall_alerts.append(FallAlert(when, payload.get("mode") == "speech"))
            fall_active = True
        elif event in ("resolved", "absence"):
            fall_active = False
        if event == "longstay":
            summary.longstay_alerts.append(when)
        if event == "resolved" and payload.get("trigger") == "noNeedHelp":
            summary.no_need_help_replies.append(when)
        if event in PRESENT_EVENTS and start is None:
            start = when
        elif event == "absence" and start is not None:
            summary.visits.append(Period(start, when))
            start = None
    if online_start is not None and last is not None:
        summary.online.append(Period(online_start, last))
        if start is not None:
            summary.ongoing_visit = Period(start, last)
    return summary


def _parse(
    records: list[dict[str, Any]], summary: Summary
) -> list[tuple[datetime, dict[str, Any], dict[str, Any]]]:
    """去重、略過伺服器下的指令、解析時間，依時間排序（同一秒保留原順序）。"""
    seen: set[str] = set()
    parsed = []
    device: str | None = None
    for record in records:
        key = json.dumps(record, sort_keys=True, ensure_ascii=False)
        if key in seen:
            summary.duplicates_removed += 1
            continue
        seen.add(key)
        topic = str(record.get("topic", ""))
        if topic.count("/") > 1:
            # 例如 radar10/<裝置>/config/set：伺服器下給裝置的指令，不是裝置送出的，也不算心跳
            continue
        if not topic:
            summary.unreadable.append(record)
            continue
        if device is None:
            device = topic
        elif topic != device:
            raise ValueError(f"一次只整理一台裝置，收到 {device} 與 {topic}")
        try:
            when = datetime.strptime(record["timestamp"], TIMESTAMP_FORMAT)
            payload = record["payload"]
            payload.get("event")
        except (KeyError, TypeError, ValueError, AttributeError):
            summary.unreadable.append(record)
            continue
        parsed.append((when, payload, record))
    parsed.sort(key=lambda item: item[0])
    return parsed
