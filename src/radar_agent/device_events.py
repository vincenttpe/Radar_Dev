"""整理裝置事件：把雲端外殼格式的紀錄整理成造訪等摘要（docs/adr/0001）。

輸入是 {"timestamp", "topic", "resolution", "payload"} 的紀錄清單（docs/05 A2、A10）；
read_records 從紀錄檔（JSON 陣列或一行一筆）讀出這份清單。
用語見 CONTEXT.md「雷達資料」。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
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
    # 來源與觸發方式照 payload 原值（data/samples_device_event/README.md）：
    # radar+timeout 雷達偵測、無回應；fusion+conversation 雷達偵測、回覆求救；speech 主動呼救
    mode: str | None = None
    trigger: str | None = None
    # 等候語音回應的秒數（trigger=timeout 時的 delaysec）；沒有或讀不懂就是 None，不猜
    delay_s: int | None = None


@dataclass(frozen=True)
class Resolution:
    """警報解除：trigger 為 noNeedHelp（語音回覆沒事）或 standUp（偵測到起身），照原值。"""
    time: datetime
    trigger: str | None


@dataclass(frozen=True)
class SettingChange:
    """裝置回報的偵測設定。fallSwitch 決定坐著要不要也算跌倒，不是偵測開關（docs/05 A31）；
    stillThreshold 是久待門檻（A26）。"""
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
    # 所有 resolved 紀錄，依時間；no_need_help_replies 是其中 trigger=noNeedHelp 的時間
    resolutions: list[Resolution] = field(default_factory=list)
    # 久待：滯留超過 stillThreshold 時裝置送出 longstay（docs/05 A26）
    longstay_alerts: list[datetime] = field(default_factory=list)
    setting_changes: list[SettingChange] = field(default_factory=list)
    errors: list[tuple[datetime, str]] = field(default_factory=list)
    # 時間或格式讀不懂的紀錄：原樣保留、不參與任何計算
    unreadable: list[dict[str, Any]] = field(default_factory=list)
    # 規格外的 event：仍算心跳，但要請人確認
    unknown_events: list[dict[str, Any]] = field(default_factory=list)
    # 最後一筆是 offline、之後沒再收到資料：資料結束前就斷線了，之後的狀況不明（REVIEW.md 3.1）
    ended_offline: datetime | None = None


def disconnected(summary: Summary) -> bool:
    """有沒有斷線：在線時段不只一段（中間斷過）、沒有資料、使用途中斷線，或資料結束前斷線。"""
    return len(summary.online) != 1 or bool(summary.interrupted_visits) or summary.ended_offline is not None


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
        summary.ended_offline = when if event == "offline" else None
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
                mode, trigger, delay = payload.get("mode"), payload.get("trigger"), payload.get("delaysec")
                summary.fall_alerts.append(FallAlert(
                    when, mode == "speech",
                    mode if isinstance(mode, str) else None,
                    trigger if isinstance(trigger, str) else None,
                    delay if isinstance(delay, int) and not isinstance(delay, bool) else None,
                ))
            fall_active = True
        elif event in ("resolved", "absence"):
            fall_active = False
        if event == "longstay":
            summary.longstay_alerts.append(when)
        if event == "resolved":
            trigger = payload.get("trigger")
            summary.resolutions.append(Resolution(when, trigger if isinstance(trigger, str) else None))
            if trigger == "noNeedHelp":
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


def summarize_days(records: list[dict[str, Any]]) -> tuple[dict[date, Summary], list[dict[str, Any]]]:
    """依日曆日切開再各自整理（以 00:00 為界，時間戳沒有時區，照原樣，docs/05 A4）。

    回傳（{日期: 該日摘要}, 時間讀不懂、分不到哪一天的紀錄）。跨過午夜的造訪會被切成兩段。
    """
    by_day: dict[date, list[dict[str, Any]]] = {}
    unplaced: list[dict[str, Any]] = []
    for record in records:
        try:
            day = datetime.strptime(str(record["timestamp"]), TIMESTAMP_FORMAT).date()
        except (KeyError, TypeError, ValueError):
            unplaced.append(record)
            continue
        by_day.setdefault(day, []).append(record)
    return {day: summarize(rs) for day, rs in sorted(by_day.items())}, unplaced


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


def read_records(path: Path) -> list[dict[str, Any]]:
    """雲端格式的紀錄檔（docs/05 A2）：JSON 陣列，或一行一筆（JSON Lines）。

    整行讀不懂就整份拒收並指出位置，不略過（REVIEW.md 3.1）；欄位讀不懂的紀錄交給 summarize 列為無法判讀。
    """
    text = path.read_text(encoding="utf-8")
    records: list[Any]
    if text.lstrip().startswith("["):
        records = json.loads(text)
    else:
        records = []
        for n, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"第 {n} 行不是 JSON（{e.msg}）") from e
    for n, record in enumerate(records, 1):
        if not isinstance(record, dict):
            raise ValueError(f"第 {n} 筆不是 {{timestamp, topic, resolution, payload}} 物件")
    return records
