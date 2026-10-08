"""甲方週報第 1 版：每週跌倒次數與活動量。

直接從動作紀錄統計，不經知識圖譜、不用 LLM，所以不受換算規則與模型影響。
建議報告（接知識圖譜）不在這一版。

輸入目前只支援「動作描述 JSON」（Timestamp／Motion／Speed／fall，見 data/samples_fall/）。
裝置事件（{"mode","event","trigger","userReply"}）沒有時間戳，無法分到哪一天，
等會議清單 A1、A3、A4 有答案再加；在那之前不猜。

統計口徑（見 ASSUMPTIONS，也會印在報告裡，避免數字被誤讀）都是第 1 版的暫定值，
等會議清單 C1、C2、C3 確認後調整。

執行：
    PYTHONPATH=src python3 -m radar_agent.weekly_report --input data/samples_fall/2026-06-03.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

SPEED_ORDER = ("Slow", "Normal", "Fast")
OTHER_SPEED = "其他"
WEEKDAYS = "一二三四五六日"

ASSUMPTIONS = [
    "跌倒次數 ＝ 動作紀錄中 fall 欄位為 yes 的筆數；裝置事件的 fall 與語音確認結果尚未納入（會議清單 C2）。",
    "活動量 ＝ 動作紀錄筆數，**不含**跌倒那一筆；沒有動作持續時間，無法算「動了多久」（會議清單 A6、C1）。",
    "時間戳是本地時間、沒有時區，照原樣分到日曆日；跨日以 00:00 為界（會議清單 A4）。",
    "一週為從指定起始日起的連續 7 天；週起始日與週報層級（每位長者或每個機構）待甲方確認（會議清單 C3）。",
    "資料沒有長者或裝置代號，整份輸入視為同一個對象（會議清單 A5）。",
]

_TS = re.compile(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2}) (\d{1,2}):(\d{2})(?: ([AaPp][Mm]))?$")


@dataclass(frozen=True)
class Record:
    at: datetime          # 本地時間，無時區
    motion: str
    speed: str | None
    fall: bool | None     # None＝這筆資料沒有 fall 欄位


def parse_timestamp(text: str) -> datetime:
    """解析 '2026/06/03 08:18 AM'。

    樣本裡有 24 小時制卻附 PM 的寫法（'17:31 PM'），小時大於 12 時忽略 AM/PM。
    解析不了就丟 ValueError。
    """
    m = _TS.match(str(text).strip())
    if not m:
        raise ValueError(f"無法解析時間：{text!r}")
    year, month, day, hour, minute = (int(g) for g in m.groups()[:5])
    suffix = (m.group(6) or "").upper()
    if suffix and 1 <= hour <= 12:
        hour = (hour % 12) + (12 if suffix == "PM" else 0)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"無法解析時間：{text!r}")
    return datetime(year, month, day, hour, minute)


def _parse_fall(value) -> bool | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"yes", "true", "1"}:
        return True
    if text in {"no", "false", "0"}:
        return False
    raise ValueError(f"無法判讀 fall 欄位：{value!r}")


def from_description_records(rows: list) -> tuple[list[Record], list[str]]:
    """動作描述 JSON → Record。無法解析的筆數不丟棄，回傳原因讓報告列出。

    fall 欄位值無法判讀時整筆略過並列出，不猜：漏算一次跌倒比多算更需要被看見。
    """
    records: list[Record] = []
    skipped: list[str] = []
    for i, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            skipped.append(f"第 {i} 筆：不是物件")
            continue
        try:
            at = parse_timestamp(row.get("Timestamp", ""))
            fall = _parse_fall(row.get("fall"))
        except ValueError as e:
            skipped.append(f"第 {i} 筆：{e}")
            continue
        speed = row.get("Speed")
        records.append(Record(at=at, motion=str(row.get("Motion", "")), speed=None if speed is None else str(speed), fall=fall))
    return records, skipped


def build_report(records: list[Record], week_start: date, skipped: list[str] | None = None, title: str | None = None) -> dict:
    """統計 week_start 起連續 7 天（含起始日、不含第 8 天 00:00）。回傳可直接序列化的 dict。"""
    start = datetime.combine(week_start, datetime.min.time())
    end = start + timedelta(days=7)
    in_week = sorted((r for r in records if start <= r.at < end), key=lambda r: r.at)

    falls = [r for r in in_week if r.fall is True]
    activity = [r for r in in_week if r.fall is not True]

    per_day = []
    for offset in range(7):
        day = week_start + timedelta(days=offset)
        per_day.append({"date": day.isoformat(), "count": sum(1 for r in activity if r.at.date() == day)})
    busiest = max(per_day, key=lambda d: d["count"]) if activity else None

    speed = {s: 0 for s in SPEED_ORDER} | {OTHER_SPEED: 0}
    for r in activity:
        speed[r.speed if r.speed in SPEED_ORDER else OTHER_SPEED] += 1

    return {
        "title": title,
        "week_start": week_start.isoformat(),
        "week_end": (week_start + timedelta(days=6)).isoformat(),
        "falls": {
            "count": len(falls),
            "events": [{"at": r.at.isoformat(timespec="minutes"), "motion": r.motion} for r in falls],
            "judgeable": any(r.fall is not None for r in in_week),
        },
        "activity": {
            "total": len(activity),
            "per_day": per_day,
            "active_days": sum(1 for d in per_day if d["count"] > 0),
            "busiest_day": busiest["date"] if busiest else None,
            "speed": speed,
        },
        "data_quality": {
            "records_in_week": len(in_week),
            "out_of_week": len(records) - len(in_week),
            "fall_field_missing": sum(1 for r in in_week if r.fall is None),
            "skipped": list(skipped or []),
        },
        "assumptions": list(ASSUMPTIONS),
    }


def _fmt_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d:%Y/%m/%d}（{WEEKDAYS[d.weekday()]}）"


def render_markdown(report: dict) -> str:
    falls, act, dq = report["falls"], report["activity"], report["data_quality"]
    head = f'{report["title"]} ' if report["title"] else ""
    out = [
        f'# {head}週報　{_fmt_date(report["week_start"])} – {_fmt_date(report["week_end"])}',
        "",
        "## 跌倒",
        "",
    ]
    if dq["records_in_week"] == 0:
        out.append("本週沒有任何紀錄，**無法判斷是否發生跌倒**（也可能是裝置沒有傳資料）。")
    elif not falls["judgeable"]:
        out.append("本週的資料沒有跌倒欄位，**無法判斷是否發生跌倒**。")
    else:
        out.append(f'本週跌倒 **{falls["count"]}** 次。')
        if dq["fall_field_missing"]:
            out.append(f'（另有 {dq["fall_field_missing"]} 筆沒有跌倒欄位，未計入。）')
        if falls["events"]:
            out.append("")
            for e in falls["events"]:
                at = datetime.fromisoformat(e["at"])
                out.append(f'- {at:%Y/%m/%d %H:%M}　原始描述：{e["motion"]}')
    out += ["", "## 活動量", ""]
    if act["total"] == 0:
        out.append("本週沒有動作紀錄。")
    else:
        out.append(f'本週動作紀錄 **{act["total"]}** 筆（不含跌倒），有紀錄的天數 {act["active_days"]} / 7，最多的一天是 {_fmt_date(act["busiest_day"])}。')
        sp = act["speed"]
        parts = [f"{k} {sp[k]}" for k in SPEED_ORDER] + ([f"{OTHER_SPEED} {sp[OTHER_SPEED]}"] if sp[OTHER_SPEED] else [])
        out.append(f'速度分布：{"、".join(parts)}。')
    out += ["", "| 日期 | 動作紀錄筆數 |", "|---|---:|"]
    out += [f'| {_fmt_date(d["date"])} | {d["count"]} |' for d in act["per_day"]]
    out += ["", "## 統計口徑與資料品質", ""]
    out += [f"- {a}" for a in report["assumptions"]]
    out.append(f'- 本週納入 {dq["records_in_week"]} 筆；週外 {dq["out_of_week"]} 筆未計；無法解析 {len(dq["skipped"])} 筆。')
    out += [f"  - {s}" for s in dq["skipped"]]
    out += ["", "建議報告尚未包含在這一版。", ""]
    return "\n".join(out)


def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="產生甲方週報（跌倒次數與活動量）")
    ap.add_argument("--input", required=True, help="動作描述 JSON 檔")
    ap.add_argument("--week-start", help="週起始日 YYYY-MM-DD；預設為最新一筆所在週的星期一")
    ap.add_argument("--title", help="報告名稱，例如機構名稱")
    ap.add_argument("--json", action="store_true", help="輸出 JSON 而不是 Markdown")
    args = ap.parse_args(argv)

    rows = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        print("輸入必須是 JSON 陣列", file=sys.stderr)
        return 2
    records, skipped = from_description_records(rows)
    if args.week_start:
        week_start = date.fromisoformat(args.week_start)
    elif records:
        week_start = _monday_of(max(r.at for r in records).date())
    else:
        print("沒有可用的紀錄，請用 --week-start 指定週起始日", file=sys.stderr)
        return 2

    report = build_report(records, week_start, skipped, args.title)
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n" if args.json else render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
