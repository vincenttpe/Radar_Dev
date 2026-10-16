"""產生 Demo 用的模擬裝置事件：一間浴廁的一個上午（docs/11 G2）。

**全部是模擬資料**，不是任何裝置收到的紀錄：裝置名稱是 DEMO-SIM-01，不是真實 MAC；
沒有任何長者資料。格式照雲端存的外殼 {timestamp, topic, resolution, payload}（docs/05 A2），
payload 照 2026-10-12 的 MQTT 訊息規格（data/samples_device_event/README.md）。

情境照計畫書的三項互補機制與「從警報到溝通」排列，見同目錄 README.md。

兩週版（docs/11 G3，應用情境 2「從日常活動發現異常」的示範）太大（約 24 萬筆），不放進 repo，
用 `--two-weeks` 產生到 outputs/（已被 git 忽略）。

重新產生（只用標準函式庫）：
    python data/samples_demo/generate.py              # 一個上午 → bathroom_morning.jsonl
    python data/samples_demo/generate.py --two-weeks  # 兩週 → outputs/bathroom_two_weeks.jsonl
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
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


# ── 兩週版 ───────────────────────────────────────────────
# 2026-10-01～10-14。白天的使用照固定時刻表（每天 5～7 次）；凌晨使用前一週大多每晚 1 次，
# 最後三天（10/12～10/14）每晚 3 次，用來示範「凌晨使用變多」。10/10 13:00～15:30 斷線，
# 用來示範在線不足的日子不列入比較。沒有任何警報，只看使用模式。
FIRST_DAY = date(2026, 10, 1)
DAYS = 14
DAYTIME = ["07:10", "09:40", "11:50", "14:20", "16:30", "18:40", "20:50"]
DAYTIME_COUNT = [6, 5, 7, 6, 6, 5, 7]          # 依星期幾輪替，第 i 天用前 n 個時刻
NIGHT = {0: ["02:10"], 1: ["02:40"], 2: [], 3: ["01:50"], 4: ["03:10"], 5: ["02:20"], 6: ["02:30"],
         7: ["02:10"], 8: ["03:00"], 9: ["02:20"], 10: ["02:50"],
         11: ["00:40", "02:30", "04:10"], 12: ["01:10", "02:50", "04:30"], 13: ["00:50", "02:40", "04:20"]}
TWO_WEEK_GAP = (date(2026, 10, 10), "13:00:00", "15:30:00")
VISIT_MINUTES = 4


def generate_two_weeks() -> list[dict]:
    starts: set[datetime] = set()
    for i in range(DAYS):
        day = FIRST_DAY + timedelta(days=i)
        times = DAYTIME[:DAYTIME_COUNT[i % 7]] + NIGHT[i]
        starts |= {datetime.combine(day, datetime.strptime(hm, "%H:%M").time()) for hm in times}
    visit = timedelta(minutes=VISIT_MINUTES)
    gap_day, gap_off, gap_on = TWO_WEEK_GAP
    off = datetime.combine(gap_day, datetime.strptime(gap_off, "%H:%M:%S").time())
    on = datetime.combine(gap_day, datetime.strptime(gap_on, "%H:%M:%S").time())
    begin = datetime.combine(FIRST_DAY, datetime.min.time())
    end = begin + timedelta(days=DAYS) - STEP
    records = [_record(begin, {"event": "online"})]
    now, current = begin + STEP, None
    while now <= end:
        if now in starts:
            current = now
        if off <= now < on:
            if now == off:
                records.append(_record(now, {"event": "offline"}))
        elif now == on:
            records.append(_record(now, {"event": "online"}))
        elif current is not None and now == current:
            records.append(_record(now, {**RADAR, "event": "presence"}))
        elif current is not None and now < current + visit:
            records.append(_record(now, {**RADAR, "event": "moving"}))
        else:
            current = None
            records.append(_record(now, {**RADAR, "event": "absence"}))
        now += STEP
    return records


def to_jsonl(records: list[dict]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in records)


if __name__ == "__main__":
    if "--two-weeks" in sys.argv[1:]:
        out = Path(__file__).resolve().parents[2] / "outputs" / "bathroom_two_weeks.jsonl"
        out.parent.mkdir(exist_ok=True)
        out.write_text(to_jsonl(generate_two_weeks()), encoding="utf-8")
    else:
        out = Path(__file__).with_name("bathroom_morning.jsonl")
        out.write_text(to_jsonl(generate()), encoding="utf-8")
    print(f"寫入 {out}")
