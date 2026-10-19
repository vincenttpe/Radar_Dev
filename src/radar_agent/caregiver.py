"""居服員 Profile：把事實（場域事件摘要、知識圖譜等級）套進固定範本。輸出檢查在 output_check.py。

docs/02「在地模型負載與分流」的範本層：範本是主要路徑，不經過模型。
原則 2：事實在上游決定；這裡只補前後句子，知識圖譜的等級與建議原文照搬。
用語於 2026-10-14 在對話中確認；改範本用語屬單向門「給讀者的輸出與用語」（REVIEW.md 第 2 節）。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any

from radar_agent.device_events import FallAlert, Summary, read_records, summarize, summarize_days
from radar_agent.kg_rules import Assessment
from radar_agent.output_check import check_output, disconnected, numbers, wording_problems


def duration_text(span: timedelta) -> str:
    """停留時間的寫法：「4 分 35 秒」「5 分鐘」「40 秒」。"""
    minutes, seconds = divmod(int(span.total_seconds()), 60)
    if minutes and seconds:
        return f"{minutes} 分 {seconds} 秒"
    if minutes:
        return f"{minutes} 分鐘"
    return f"{seconds} 秒"


# ── 場域摘要的句子：radar-caregiver 的【事件摘要】與 radar-ask 的答案共用 ──
# 同一個事實只有一種寫法；斷線與無法判讀的提醒放在這裡，兩邊都不會漏（REVIEW.md 3.1）。

def falls_and_calls(summary: Summary) -> tuple[list[FallAlert], list[FallAlert]]:
    """跌倒警報與主動呼救分開（mode=speech 的 fall 是主動呼救，不算跌倒；docs/05 C2）。"""
    return ([a for a in summary.fall_alerts if not a.distress_call],
            [a for a in summary.fall_alerts if a.distress_call])


def period_head(summary: Summary, place: str) -> str:
    """「1 號廁所 10/14：」；跨日寫成「10/13–10/14」。summary.online 不能是空的。"""
    first, last = summary.online[0].start, summary.online[-1].end
    days = f"{first.month}/{first.day}"
    if last.date() != first.date():
        days += f"–{last.month}/{last.day}"
    return f"{place} {days}："


def count_sentence(summary: Summary, label: str, times: list[datetime]) -> str:
    """「跌倒警報 2 次（08:32、09:16）。」；裝置有斷線時前面加「在線期間」。"""
    scope = "在線期間" if disconnected(summary) else ""
    listed = f"（{'、'.join(f'{t:%H:%M}' for t in times)}）" if times else ""
    return f"{scope}{label} {len(times)} 次{listed}。"


def usage_sentences(summary: Summary) -> list[str]:
    if summary.visits:
        longest = max(v.duration for v in summary.visits)
        lines = [f"有人使用 {len(summary.visits)} 次，最長一次 {duration_text(longest)}。"]
    else:
        lines = ["沒有偵測到有人使用。"]
    if summary.interrupted_visits:
        lines.append(f"另有 {len(summary.interrupted_visits)} 次使用途中裝置斷線，停留時間不明。")
    if summary.ongoing_visit is not None:
        lines.append(f"資料結束時仍有人在內（{summary.ongoing_visit.start:%H:%M} 起）。")
    return lines


def offline_sentences(summary: Summary, what: str = "是否有跌倒") -> list[str]:
    """斷線時段；沒有斷線時回傳空清單。資料結束前斷線、之後沒再連上，也算斷線。"""
    gaps = list(pairwise(summary.online))
    if gaps:
        return [f"{a.end:%H:%M}–{b.start:%H:%M} 裝置斷線，這段時間無法判斷{what}。" for a, b in gaps]
    if disconnected(summary):
        return [f"{summary.online[-1].end:%H:%M} 起裝置斷線，之後無法判斷{what}。"]
    return []


def device_problem_sentences(summary: Summary) -> list[str]:
    """裝置錯誤與讀不懂的紀錄：都要請資訊人員處理，不能省略。"""
    lines = []
    if summary.errors:
        times = "、".join(f"{when:%H:%M}" for when, _ in summary.errors)
        lines.append(f"裝置回報錯誤 {len(summary.errors)} 次（{times}），請通知資訊人員。")
    unreadable = len(summary.unreadable) + len(summary.unknown_events)
    if unreadable:
        lines.append(f"另有 {unreadable} 筆紀錄無法判讀，請通知資訊人員。")
    return lines


def _site_text(summary: Summary, place: str) -> str:
    if not summary.online:
        return f"{place}：沒有收到資料，無法判斷。"
    falls, calls = falls_and_calls(summary)
    parts = [period_head(summary, place), *usage_sentences(summary),
             count_sentence(summary, "跌倒警報", [a.time for a in falls]),
             count_sentence(summary, "主動呼救", [a.time for a in calls])]
    if summary.no_need_help_replies:
        parts.append(f"語音回覆沒事 {len(summary.no_need_help_replies)} 次。")
    parts.append(count_sentence(summary, "久待警報", summary.longstay_alerts))
    parts += offline_sentences(summary) or ["這段時間裝置都在線。"]
    parts += device_problem_sentences(summary)
    return "".join(parts)


def _personal_text(a: Assessment) -> str:
    unit = f' {a["unit"]}' if a["unit"] else ""
    head = f'{a["metric"]} {a["value"]}{unit}'
    if a["status"] == "assessed":
        lines = [f'{head}，屬於「{a["rank"]}」（{a["score"]} 分）。', f'建議：{a["suggestion"]}']
        if a["recommended_dose"]:
            lines.append(f'建議量：{a["recommended_dose"]}')
    elif a["status"] == "no_rule":
        lines = [f'{a["metric"]}：目前沒有評估規則，已送審核。']
    else:
        # 缺口、重疊、精度等原因屬審核用語，不給居服員；也不列候選等級
        lines = [f"{head}：目前無法評估，已交由審核人確認。"]
    if not a["kg"]["approved"]:
        lines.append("（知識圖譜尚未核准，僅供測試）")
    return "\n".join(lines)


def caregiver_text(source: Summary | Assessment, *, place: str = "") -> str:
    """居服員看的文字。Summary → 場域事件摘要（place 為地點名稱，不含個人資料）；Assessment → 個人等級與建議。"""
    if isinstance(source, Summary):
        return _site_text(source, place)
    return _personal_text(source)


# ── 逐事件的處置提示與照護紀錄草稿 ─────────────────────────
# 計畫書「決策建議輸出」：事件摘要、處置提示、照護紀錄草稿（docs/11 G1）。
# 用語於 2026-10-16 在對話中確認（docs/11「G1 用語」）。只寫裝置事件裡有的事實：
# 沒有姿態持續時間，所以不寫計畫書例句的「持續 30 秒」。

@dataclass(frozen=True)
class EventNote:
    time: datetime
    prompt: str   # 處置提示
    record: str   # 照護紀錄草稿
    span: str = ""  # 處置提示前面標的時間；空的就用 time 的 HH:MM（斷線標起訖）


def _fall_note(alert: FallAlert, place: str) -> EventNote:
    when = f"{alert.time:%H:%M}"
    if alert.mode == "speech":
        return EventNote(alert.time, "長者主動呼救，請立即到場。", f"{when} {place}偵測到主動呼救。")
    if alert.mode == "fusion":
        return EventNote(
            alert.time, "長者回覆需要協助，請立即到場。", f"{when} {place}雷達偵測到疑似跌倒，長者語音回覆需要協助。"
        )
    if alert.mode == "radar" and alert.trigger == "timeout":
        waited = f" {alert.delay_s} 秒" if alert.delay_s is not None else ""
        return EventNote(
            alert.time, "請立即到場確認。", f"{when} {place}雷達偵測到疑似跌倒，語音詢問{waited}未獲回應。"
        )
    # 規格外的來源組合：不猜是哪一種，一律當成要到場確認
    return EventNote(alert.time, "請立即到場確認。", f"{when} {place}跌倒警報。")


def event_notes(summary: Summary, place: str) -> list[EventNote]:
    """每個事件一則：跌倒警報、警報解除、久待警報、裝置斷線，依時間排列。"""
    notes = [_fall_note(a, place) for a in summary.fall_alerts]
    for r in summary.resolutions:
        when = f"{r.time:%H:%M}"
        if r.trigger == "noNeedHelp":
            notes.append(EventNote(r.time, "長者回覆沒事，警報已解除；可視情況前往關心。", f"{when} 長者語音回覆沒事，警報解除。"))
        elif r.trigger == "standUp":
            notes.append(EventNote(r.time, "已偵測到起身，警報已解除。", f"{when} 偵測到長者起身，警報解除。"))
    for t in summary.longstay_alerts:
        notes.append(EventNote(t, "停留超過設定時間，請前往查看。", f"{t:%H:%M} {place}停留超過設定時間（久待警報）。"))
    for a, b in zip(summary.online, summary.online[1:], strict=False):
        span = f"{a.end:%H:%M}–{b.start:%H:%M}"
        notes.append(EventNote(
            a.end, "這段時間無法判斷是否有跌倒，請通知資訊人員。", f"{span} 裝置斷線，期間狀況不明。", span
        ))
    return sorted(notes, key=lambda n: n.time)


def caregiver_report(summary: Summary, *, place: str) -> str:
    """事件摘要＋處置提示＋照護紀錄草稿。沒有事件時只有摘要，不另寫「沒有事件」。"""
    parts = ["【事件摘要】", _site_text(summary, place)]
    notes = event_notes(summary, place)
    if notes:
        parts += ["", "【處置提示】", *(f"{n.span or f'{n.time:%H:%M}'} {n.prompt}" for n in notes)]
        parts += ["", "【照護紀錄草稿】", *(n.record for n in notes)]
    return "\n".join(parts)


# ── 給 Dashboard 的結構化輸出（docs/16_Dashboard對接說明.md）──────────
# 文字與 caregiver_report 逐句相同，另附計數與每則事件的等級，讓 Dashboard 自己排版。
REPORT_SCHEMA = "caregiver-report-v0"
TREND_SCHEMA = "usage-trend-v0"

# 處置提示 → （等級, 標題）。標題用計畫書三項機制的名稱；等級給 Dashboard 決定顏色：
# urgent 立即到場、check 前往查看、resolved 警報解除、device 裝置問題。
# 上面的處置提示一改，這裡認不得就會丟錯，提醒同步更新。
NOTE_KINDS: dict[str, tuple[str, str]] = {
    "請立即到場確認。": ("urgent", "跌倒警報"),
    "長者回覆需要協助，請立即到場。": ("urgent", "跌倒警報"),
    "長者主動呼救，請立即到場。": ("urgent", "主動呼救"),
    "長者回覆沒事，警報已解除；可視情況前往關心。": ("resolved", "回覆沒事"),
    "已偵測到起身，警報已解除。": ("resolved", "起身解除"),
    "停留超過設定時間，請前往查看。": ("check", "久待警報"),
    "這段時間無法判斷是否有跌倒，請通知資訊人員。": ("device", "裝置斷線"),
}


class OutputRejected(ValueError):
    """輸出檢查沒通過：不交出任何文字。problems 是檢查列出的問題。"""

    def __init__(self, problems: list[str]):
        super().__init__("輸出檢查沒通過：" + "；".join(problems))
        self.problems = problems


def note_kind(prompt: str) -> tuple[str, str]:
    """處置提示的（等級, 標題）；認不得就丟 ValueError，提醒同步更新 NOTE_KINDS。"""
    if prompt not in NOTE_KINDS:
        raise ValueError(f"NOTE_KINDS 沒有這句處置提示：{prompt}")
    return NOTE_KINDS[prompt]


def _stamp(when: datetime) -> str:
    return f"{when:%Y-%m-%d %H:%M:%S}"


def report_data(summary: Summary, *, place: str) -> dict[str, Any]:
    """caregiver_report 的結構化版本。輸出檢查沒通過時丟 OutputRejected。"""
    problems = check_output(caregiver_report(summary, place=place), summary, place=place)
    if problems:
        raise OutputRejected(problems)
    events = []
    for note in event_notes(summary, place):
        level, title = note_kind(note.prompt)
        events.append({
            "at": _stamp(note.time), "label": note.span or f"{note.time:%H:%M}", "level": level, "title": title,
            "prompt": note.prompt, "record": note.record,
        })
    online = summary.online
    return {
        "schema": REPORT_SCHEMA,
        "place": place,
        "from": _stamp(online[0].start) if online else None,
        "to": _stamp(online[-1].end) if online else None,
        "summary": _site_text(summary, place),
        "counts": {
            "visits": len(summary.visits),
            "fall_alerts": len(falls_and_calls(summary)[0]),
            "distress_calls": len(falls_and_calls(summary)[1]),
            "no_need_help": len(summary.no_need_help_replies),
            "longstay": len(summary.longstay_alerts),
            "offline": [[_stamp(a.end), _stamp(b.start)] for a, b in pairwise(online)],
        },
        "events": events,
        "text_layer": "template",
    }


# ── 使用趨勢：近 7 天與前 7 天比較（docs/11 G3）────────────────
# 計畫書應用情境 2「從日常活動發現異常」的最小版。場域層級：沒有個人 ID，不推測是哪一位長者。
# 下列門檻與用語於 2026-10-16 在對話中確認（docs/11「G3 規則」）；審核人另行核准前仍可調整。
# 只陳述增加／減少，不接提醒句；跨過午夜的使用切成兩天，可能各算一次（已確認可接受）。
FULL_DAY_ONLINE = timedelta(hours=23)   # 當天在線不到這麼久就不列入比較（沒有資料不等於沒有使用）
MIN_COUNTED_DAYS = 5                     # 每一週至少要有幾天可比較
CHANGE_AT_LEAST = Decimal(1)             # 平均每日差距至少 1 次……
# ……而且至少是前一週的這個比例，才寫「增加／減少」。每日總次數 30%（2026-10-16 在對話中決定），凌晨 50%
CHANGE_RATIO = {"visits": Decimal("0.3"), "dawn": Decimal("0.5")}
DAWN_END_HOUR = 6                        # 凌晨：00:00–06:00


@dataclass(frozen=True)
class DayCount:
    day: date
    visits: int | None   # None：當天在線不足或沒有資料，不列入比較
    dawn: int | None


@dataclass(frozen=True)
class Trend:
    recent: list[DayCount]   # 最後 7 天，依日期
    before: list[DayCount]   # 再往前 7 天
    unplaced: int            # 時間讀不懂、分不到哪一天的紀錄


def _day_count(day: date, summary: Summary | None) -> DayCount:
    if summary is None or sum((p.duration for p in summary.online), timedelta()) < FULL_DAY_ONLINE:
        return DayCount(day, None, None)
    starts = [v.start for v in summary.visits + summary.interrupted_visits]
    if summary.ongoing_visit is not None:
        starts.append(summary.ongoing_visit.start)
    return DayCount(day, len(starts), sum(t.hour < DAWN_END_HOUR for t in starts))


def usage_trend(records: list[dict[str, Any]]) -> Trend | None:
    """依最後一天往前切兩週；完全沒有可分日的紀錄時回傳 None。"""
    days, unplaced = summarize_days(records)
    if not days:
        return None
    last = max(days)
    window = [last - timedelta(days=13 - i) for i in range(14)]
    counts = [_day_count(d, days.get(d)) for d in window]
    return Trend(counts[7:], counts[:7], len(unplaced))


def _average(counts: list[DayCount], field: str) -> Decimal | None:
    values = [getattr(c, field) for c in counts if getattr(c, field) is not None]
    if len(values) < MIN_COUNTED_DAYS:
        return None
    return Decimal(sum(values)) / len(values)


def _one(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _direction(recent: Decimal, before: Decimal, ratio: Decimal) -> str:
    diff = recent - before
    if abs(diff) >= CHANGE_AT_LEAST and (before == 0 or abs(diff) / before >= ratio):
        return "增加" if diff > 0 else "減少"
    return "差不多"


def _md(d: date) -> str:
    return f"{d.month}/{d.day}"


def trend_text(trend: Trend | None, *, place: str) -> str:
    if trend is None:
        return f"【使用趨勢】{place}：沒有收到資料，無法判斷。"
    r, b = trend.recent, trend.before
    lines = [f"【使用趨勢】{place} {_md(r[0].day)}–{_md(r[-1].day)} 與前一週（{_md(b[0].day)}–{_md(b[-1].day)}）比較："]
    rows = [("每日使用次數", "visits"), (f"凌晨（00:00–0{DAWN_END_HOUR}:00）使用次數", "dawn")]
    averages = [(label, f, _average(r, f), _average(b, f)) for label, f in rows]
    if any(ra is None or ba is None for _, _, ra, ba in averages):
        n_r = sum(c.visits is not None for c in r)
        n_b = sum(c.visits is not None for c in b)
        lines.append(f"可比較的天數不足（近一週 {n_r} 天、前一週 {n_b} 天，各需 {MIN_COUNTED_DAYS} 天），無法比較。")
    else:
        for label, f, ra, ba in averages:
            assert ra is not None and ba is not None
            lines.append(f"{label}：平均每日 {_one(ra)} 次，前一週 {_one(ba)} 次，{_direction(ra, ba, CHANGE_RATIO[f])}。")
    days = [
        f"{_md(c.day)} {c.visits} 次（凌晨 {c.dawn}）" if c.visits is not None else f"{_md(c.day)} 在線不足，不列入"
        for c in r
    ]
    lines.append("近 7 天：" + "、".join(days) + "。")
    if trend.unplaced:
        lines.append(f"另有 {trend.unplaced} 筆紀錄時間無法判讀，請通知資訊人員。")
    lines.append("這是場域的統計，沒有個人 ID，不代表特定長者；供照護人員參考。")
    return "\n".join(lines)


def check_trend(text: str, trend: Trend | None, *, place: str = "") -> list[str]:
    """使用趨勢的輸出檢查：數字都要來自統計，用語規則同 output_check.check_output。"""
    allowed = numbers(place) | {Decimal(0), Decimal(DAWN_END_HOUR), Decimal(MIN_COUNTED_DAYS), Decimal(7)}
    if trend is not None:
        for c in trend.recent + trend.before:
            allowed |= {Decimal(c.day.month), Decimal(c.day.day)}
            allowed |= {Decimal(n) for n in (c.visits, c.dawn) if n is not None}
        allowed |= {Decimal(sum(c.visits is not None for c in w)) for w in (trend.recent, trend.before)}
        for f in ("visits", "dawn"):
            for w in (trend.recent, trend.before):
                avg = _average(w, f)
                if avg is not None:
                    allowed.add(Decimal(_one(avg)))
        allowed.add(Decimal(trend.unplaced))
    problems = [f"數字 {n} 不在統計裡" for n in sorted(numbers(text) - allowed)]
    return problems + wording_problems(text)


# ── 命令列 ───────────────────────────────────────────────
# 裝置事件檔 → read_records → summarize → caregiver_text → check_output；檢查沒通過就不輸出文字。

def trend_data(trend: Trend | None, *, place: str) -> dict[str, Any]:
    """trend_text 的結構化版本：文字逐行相同，另附每天的次數（None 表示在線不足、不列入比較）。"""
    text = trend_text(trend, place=place)
    problems = check_trend(text, trend, place=place)
    if problems:
        raise OutputRejected(problems)
    days = [] if trend is None else [
        {"day": c.day.isoformat(), "visits": c.visits, "dawn": c.dawn, "recent": i >= 7}
        for i, c in enumerate(trend.before + trend.recent)
    ]
    return {"schema": TREND_SCHEMA, "place": place, "lines": text.splitlines(), "days": days, "text_layer": "template"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="一台裝置的事件 → 居服員看的事件摘要、處置提示與照護紀錄草稿（範本，不用模型；先過輸出檢查）"
    )
    ap.add_argument("events", type=Path, help="紀錄檔：雲端格式 {timestamp, topic, resolution, payload}，JSON 陣列或一行一筆")
    ap.add_argument("--place", required=True, help="地點名稱，例如「1 號廁所」；不放長者姓名等個人資料")
    ap.add_argument("--trend", action="store_true", help="改輸出使用趨勢：最後 7 天與前 7 天比較（需要約兩週的紀錄）")
    ap.add_argument("--json", action="store_true", help="改輸出 JSON 給 Dashboard（欄位見 docs/16_Dashboard對接說明.md）")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    if not args.place.strip():
        print("錯誤：--place 不能是空白", file=sys.stderr)
        return 2
    problems: list[str] = []
    try:
        records = read_records(args.events)
        if args.json:
            try:
                data = (trend_data(usage_trend(records), place=args.place) if args.trend
                        else report_data(summarize(records), place=args.place))
                text = json.dumps(data, ensure_ascii=False, indent=2)
            except OutputRejected as e:
                problems = e.problems
        elif args.trend:
            trend = usage_trend(records)
            text = trend_text(trend, place=args.place)
            problems = check_trend(text, trend, place=args.place)
        else:
            summary = summarize(records)
            text = caregiver_report(summary, place=args.place)
            problems = check_output(text, summary, place=args.place)
    except (OSError, ValueError) as e:
        print(f"錯誤：{args.events}：{e}", file=sys.stderr)
        return 2
    if problems:
        print("輸出檢查沒通過，不輸出給居服員：", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
