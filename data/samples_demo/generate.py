"""產生 Demo 用的模擬裝置事件：一間浴廁的一個上午（docs/11 G2）。

**全部是模擬資料**，不是任何裝置收到的紀錄：裝置名稱是 DEMO-SIM-01，不是真實 MAC；
沒有任何長者資料。格式照雲端存的外殼 {timestamp, topic, resolution, payload}（docs/05 A2），
payload 照 2026-10-12 的 MQTT 訊息規格（data/samples_device_event/README.md）。

情境照計畫書的三項互補機制與「從警報到溝通」排列，見同目錄 README.md。

重新產生（只用標準函式庫）：
    python data/samples_demo/generate.py
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

DAY = "2026-10-14"
TOPIC = "radar10/DEMO-SIM-01"
START, END = "08:00:00", "11:59:55"
STEP = timedelta(seconds=5)  # 實測心跳約每 5 秒一筆（docs/05 A10）
# 斷線：裝置在 11:10 斷線（broker 送 offline），11:25 重新上線
OFFLINE, ONLINE = "11:10:00", "11:25:00"

# 有人在場的時段（含頭不含尾），期間送 moving；靜止滯留的那一段送 static
VISITS = [
    ("08:05:00", "08:09:00", "moving"),   # 一般使用
    ("08:31:00", "08:35:00", "moving"),   # 情境 A
    ("09:15:00", "09:21:00", "moving"),   # 情境 B
    ("10:02:00", "10:07:00", "moving"),   # 情境 C
    ("10:30:00", "10:52:00", "static"),   # 情境 D：靜止滯留
]

RADAR = {"mode": "radar", "trigger": "track", "userReply": False}
# 情境中的警報與解除，payload 照規格表逐字
EVENTS = [
    # A：雷達偵測疑似跌倒，等 10 秒沒回應 → 稍後長者回覆沒事
    ("08:32:10", {"mode": "radar", "event": "fall", "trigger": "timeout", "userReply": False, "delaysec": 10, "confidence": 100}),
    ("08:32:40", {"mode": "speech", "event": "resolved", "trigger": "noNeedHelp", "userReply": True, "confidence": 100}),
    # B：雷達偵測疑似跌倒，長者回覆需要協助 → 照護人員到場後起身
    ("09:16:20", {"mode": "fusion", "event": "fall", "trigger": "conversation", "userReply": True, "confidence": 100}),
    ("09:19:30", {"mode": "radar", "event": "resolved", "trigger": "standUp", "userReply": False, "confidence": 100}),
    # C：長者主動呼救（語音確認）→ 照護人員到場後起身
    ("10:03:15", {"mode": "speech", "event": "fall", "trigger": "conversation", "userReply": True, "confidence": 100}),
    ("10:05:50", {"mode": "radar", "event": "resolved", "trigger": "standUp", "userReply": False, "confidence": 100}),
    # D：靜止滯留超過設定時間
    ("10:45:00", {"mode": "radar", "event": "longstay", "trigger": "timeout", "userReply": False, "delaysec": 600, "confidence": 100}),
]


def _t(hms: str) -> datetime:
    return datetime.strptime(f"{DAY} {hms}", "%Y-%m-%d %H:%M:%S")


def _record(when: datetime, payload: dict) -> dict:
    return {"timestamp": when.strftime("%Y-%m-%d %H:%M:%S"), "topic": TOPIC, "resolution": None, "payload": payload}


def _state(when: datetime) -> str:
    for start, end, inside in VISITS:
        if when == _t(start):
            return "presence"
        if _t(start) < when < _t(end):
            return inside
        if when == _t(end):
            return "absence"
    return "absence"


def generate() -> list[dict]:
    records = [_record(_t(START), {"event": "online"})]
    now = _t(START) + STEP
    while now <= _t(END):
        if _t(OFFLINE) <= now < _t(ONLINE):
            if now == _t(OFFLINE):
                records.append(_record(now, {"event": "offline"}))
        elif now == _t(ONLINE):
            records.append(_record(now, {"event": "online"}))
        else:
            records.append(_record(now, {**RADAR, "event": _state(now)}))
        now += STEP
    records += [_record(_t(hms), payload) for hms, payload in EVENTS]
    records.sort(key=lambda r: r["timestamp"])  # 同一秒保留加入順序
    return records


def to_jsonl(records: list[dict]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in records)


if __name__ == "__main__":
    out = Path(__file__).with_name("bathroom_morning.jsonl")
    out.write_text(to_jsonl(generate()), encoding="utf-8")
    print(f"寫入 {out}")
