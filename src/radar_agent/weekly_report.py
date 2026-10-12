"""甲方週報第 1 版：每週跌倒次數與活動量。

直接從動作紀錄統計，不經知識圖譜、不用 LLM，所以不受換算規則與模型影響。
建議報告（接知識圖譜）不在這一版。

輸入目前只支援「動作描述 JSON」（Timestamp／Motion／Speed／fall，見 data/samples_fall/）。
裝置事件（{"mode","event","trigger","userReply"}）沒有時間戳，無法分到哪一天，
等會議清單 A1、A3、A4 有答案再加；在那之前不猜。

結構（要改統計口徑時看這裡）：
    算不算跌倒、算不算活動量：counts_as_fall()、counts_as_activity()（會議清單 C1、C2）；
    其他會改變數字的地方見 REVIEW.md 3.3。
    統計：summarize_falls / summarize_activity / summarize_quality，由 build_report 組合。
    報告裡的每個數字都在這裡算好，--json 與 Markdown 讀的是同一份。
    輸出：render_markdown 只排版：數字一律讀 WeeklyReport 的欄位，不自己計數，兩種輸出才不會不一致。
    報告的欄位定義在 WeeklyReport（TypedDict）；統計、Markdown、--json 與測試都以它為準。

統計口徑（見 ASSUMPTIONS，也會印在報告裡，避免數字被誤讀）都是第 1 版的暫定值，
等會議清單 C1、C2、C3 確認後調整。

執行：
    radar-weekly-report --input data/samples_fall/2026-06-03.json   （需要先 pip install -e .）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal, TypedDict

SPEED_ORDER = ("Slow", "Normal", "Fast")
# 逐日表格只計入活動量（fall 為 no）的紀錄：當天收到的跌倒、無法判讀的紀錄都不算，所以 0 不等於「沒收到」
DAILY_ZERO_NOTE = "0 表示該日沒有納入活動統計的紀錄，不代表沒有活動，也不能據此判定裝置是否在線（會議清單 D3）。"
OTHER_SPEED = "其他"
WEEKDAYS = "一二三四五六日"

ASSUMPTIONS = [
    "跌倒次數 ＝ 動作紀錄中 fall 欄位為 yes 的筆數；裝置事件的 fall 與語音確認結果尚未納入（會議清單 C2）。只要本週有任何一筆無法判讀（沒有 fall 欄位、值無法辨識、時間無法解析），這個數字只是下限，報告會註明「全週總數無法確認」。",
    "活動量 ＝ fall 欄位為 no 的動作紀錄筆數；跌倒那一筆不算，無法判讀是否為跌倒的紀錄也不算（另列筆數，不當成一般活動）。沒有動作持續時間，無法算「動了多久」（會議清單 A6、C1）。",
    "時間戳是本地時間、沒有時區，照原樣分到日曆日；跨日以 00:00 為界（會議清單 A4）。",
    "一週為從指定起始日起的連續 7 天；週起始日與週報層級（每位長者或每個機構）待甲方確認（會議清單 C3）。",
    "資料沒有長者或裝置代號，整份輸入視為同一個對象（會議清單 A5）。",
]

_TS = re.compile(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2}) (\d{1,2}):(\d{2})(?: ([AaPp][Mm]))?$")


# ── 資料型別 ──────────────────────────────────────────────

@dataclass(frozen=True)
class Record:
    at: datetime          # 本地時間，無時區
    motion: str
    speed: str | None
    fall: bool | None     # None＝無法判讀：沒有 fall 欄位，或值無法辨識（見 fall_raw）
    fall_raw: str | None = None  # fall 值無法辨識時保留原值，報告逐筆列出；其他情況為 None


@dataclass(frozen=True)
class Skipped:
    """讀不進統計的一筆。時間無法解析時仍保留 Motion 與 fall，報告才能指出被略過的是不是跌倒。"""
    row: int              # 輸入陣列中的第幾筆（從 1 起算）
    kind: Literal["not_object", "bad_timestamp"]
    reason: str
    timestamp: str = ""
    motion: str = ""
    fall: bool | None = None
    fall_raw: str | None = None   # fall 值無法辨識時的原值

    @property
    def message(self) -> str:
        return f"第 {self.row} 筆：{self.reason}"

    @property
    def possible_fall(self) -> bool:
        """標記為跌倒，或 fall 值無法辨識（可能就是跌倒）。"""
        return self.fall is True or self.fall_raw is not None


class FallEvent(TypedDict):
    at: str               # ISO 8601，到分鐘
    motion: str


class UnreadableFall(TypedDict):
    at: str               # ISO 8601，到分鐘
    motion: str
    raw: str              # 無法辨識的 fall 原值


class UndatedFall(TypedDict):
    row: int              # 輸入陣列中的第幾筆
    timestamp: str        # 無法解析的時間原文
    motion: str
    fall: str             # fall 原值（yes 或無法辨識的值）


Completeness = Literal["all_readable", "partial", "none"]


class FallSummary(TypedDict):
    count: int            # 輸入的紀錄中標記為跌倒的筆數；completeness 不是 all_readable 時只是下限
    events: list[FallEvent]
    judgeable: bool       # 至少有一筆可判讀；False＝不能把「沒資料」當成「沒有跌倒」
    # all_readable＝輸入的本週紀錄每筆都可判讀（不代表裝置整週在線，資料可能有缺漏）；
    # partial＝有些不行；none＝完全無法判斷
    completeness: Completeness
    unreadable: list[UnreadableFall]  # fall 值無法辨識的本週紀錄，逐筆列出（可能就是跌倒）
    undated: list[UndatedFall]  # 時間無法解析、但標記為跌倒或 fall 值無法辨識的紀錄（可能屬於本週）
    undated_marked_fall: int    # undated 之中 fall 標記為 yes 的筆數
    outside_week: list[FallEvent]  # 本週之外的跌倒紀錄，未計入本週，但逐筆列出避免週起始日選錯時被埋掉


class DayCount(TypedDict):
    date: str
    count: int


class ActivitySummary(TypedDict):
    total: int            # 可判讀且不是跌倒的紀錄；complete 為 False 時只是下限
    complete: bool        # 本週每筆都能判讀是否為跌倒，且沒有時間無法解析的紀錄
    unjudged: int         # 無法判讀是否為跌倒或屬於哪一天、因此未計入 total 的筆數（等於 data_quality.unjudged_total）
    per_day: list[DayCount]
    active_days: int      # 有動作紀錄（計入 total）的天數；只收到跌倒或無法判讀紀錄的日子不算
    busiest_day: str | None
    speed: dict[str, int]


class DataQuality(TypedDict):
    records_in_week: int
    out_of_week: int
    fall_field_missing: int
    fall_unreadable: int
    bad_timestamp: int    # 時間無法解析，可能屬於本週
    not_object: int       # 不是 JSON 物件，內容完全無法判讀
    unjudged_total: int   # 無法判讀、未計入統計的筆數：上面四類（fall 欄位缺漏、無法辨識、時間無法解析、不是物件）的合計
    skipped: list[str]    # 上面兩類逐筆的說明


class WeeklyReport(TypedDict):
    title: str | None
    week_start: str
    week_end: str
    falls: FallSummary
    activity: ActivitySummary
    data_quality: DataQuality
    assumptions: list[str]


# ── 讀入 ─────────────────────────────────────────────────

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


def _parse_fall(value: object) -> bool | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"yes", "true", "1"}:
        return True
    if text in {"no", "false", "0"}:
        return False
    raise ValueError(f"無法判讀 fall 欄位：{value!r}")


def _read_fall(row: dict[str, object]) -> tuple[bool | None, str | None]:
    """(fall, fall_raw)：值無法辨識時 fall=None、fall_raw=原值；沒有欄位時兩者皆 None。"""
    try:
        return _parse_fall(row.get("fall")), None
    except ValueError:
        return None, str(row.get("fall"))


def from_description_records(rows: Sequence[object]) -> tuple[list[Record], list[Skipped]]:
    """動作描述 JSON → Record。時間無法解析的筆數不丟棄，回傳 Skipped 讓報告列出。

    fall 值無法辨識時不猜，也不整筆丟掉：保留這筆（fall=None、fall_raw=原值），
    報告在跌倒一節逐筆列出，並把全週跌倒數標成「無法確認」。漏算一次跌倒比多算更需要被看見。
    時間無法解析的那筆仍讀 Motion 與 fall，標記為跌倒時報告會指出來。
    """
    records: list[Record] = []
    skipped: list[Skipped] = []
    for i, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            skipped.append(Skipped(row=i, kind="not_object", reason="不是物件"))
            continue
        fall, fall_raw = _read_fall(row)
        motion = str(row.get("Motion", ""))
        try:
            at = parse_timestamp(row.get("Timestamp", ""))
        except ValueError as e:
            skipped.append(Skipped(
                row=i, kind="bad_timestamp", reason=str(e), timestamp=str(row.get("Timestamp", "")),
                motion=motion, fall=fall, fall_raw=fall_raw,
            ))
            continue
        speed = row.get("Speed")
        records.append(Record(
            at=at, motion=motion, speed=None if speed is None else str(speed), fall=fall, fall_raw=fall_raw,
        ))
    return records, skipped


# ── 統計口徑：算不算跌倒、算不算活動量（其他會改變數字的地方見 REVIEW.md 3.3）─────────

def counts_as_fall(record: Record) -> bool:
    """這一筆算不算一次跌倒。目前：fall 欄位明確為 yes。"""
    return record.fall is True


def counts_as_activity(record: Record) -> bool:
    """這一筆算不算活動量。目前：fall 明確為 no 的動作紀錄。

    無法判讀是否為跌倒的紀錄不算：跌倒一節把它列為「可能就是跌倒」，這裡就不能當成一般活動。
    """
    return record.fall is False


# ── 統計 ─────────────────────────────────────────────────

def _in_week(record: Record, week_start: date) -> bool:
    """week_start 起連續 7 天：含起始日 00:00、不含第 8 天 00:00。"""
    start = datetime.combine(week_start, datetime.min.time())
    return start <= record.at < start + timedelta(days=7)


def records_in_week(records: list[Record], week_start: date) -> list[Record]:
    """本週的紀錄，依時間排序。"""
    return sorted((r for r in records if _in_week(r, week_start)), key=lambda r: r.at)


def fall_completeness(in_week: list[Record], skipped: Sequence[Skipped]) -> Completeness:
    """本週的跌倒數能不能當成全週總數。

    時間無法解析的筆數不知道屬不屬於本週，保守地當成「可能屬於」。
    """
    if not any(r.fall is not None for r in in_week):
        return "none"
    if skipped or any(r.fall is None for r in in_week):
        return "partial"
    return "all_readable"


def _event(r: Record) -> FallEvent:
    return {"at": r.at.isoformat(timespec="minutes"), "motion": r.motion}


def summarize_falls(
    in_week: list[Record], skipped: Sequence[Skipped] = (), out_of_week: Sequence[Record] = (),
) -> FallSummary:
    falls = [r for r in in_week if counts_as_fall(r)]
    completeness = fall_completeness(in_week, skipped)
    return {
        "count": len(falls),
        "events": [_event(r) for r in falls],
        "judgeable": completeness != "none",
        "completeness": completeness,
        "unreadable": [
            {"at": r.at.isoformat(timespec="minutes"), "motion": r.motion, "raw": r.fall_raw}
            for r in in_week if r.fall_raw is not None
        ],
        "undated": [
            {"row": s.row, "timestamp": s.timestamp, "motion": s.motion, "fall": "yes" if s.fall else str(s.fall_raw)}
            for s in skipped if s.possible_fall
        ],
        "undated_marked_fall": sum(1 for s in skipped if s.fall is True),
        "outside_week": [_event(r) for r in sorted(out_of_week, key=lambda r: r.at) if counts_as_fall(r)],
    }


def daily_counts(activity: list[Record], week_start: date) -> list[DayCount]:
    """7 天各一筆，沒有紀錄的日子也列出（count 為 0）。"""
    days = [week_start + timedelta(days=offset) for offset in range(7)]
    return [{"date": day.isoformat(), "count": sum(1 for r in activity if r.at.date() == day)} for day in days]


def speed_distribution(activity: list[Record]) -> dict[str, int]:
    counts = dict.fromkeys(SPEED_ORDER, 0) | {OTHER_SPEED: 0}
    for r in activity:
        counts[r.speed if r.speed in SPEED_ORDER else OTHER_SPEED] += 1
    return counts


def unjudged_total(in_week: list[Record], skipped: Sequence[Skipped]) -> int:
    """無法判讀、未計入統計的筆數：本週 fall 欄位缺漏或無法辨識的紀錄，加上讀不進統計的紀錄（可能屬於本週）。"""
    return sum(1 for r in in_week if r.fall is None) + len(skipped)


def summarize_activity(in_week: list[Record], week_start: date, skipped: Sequence[Skipped] = ()) -> ActivitySummary:
    activity = [r for r in in_week if counts_as_activity(r)]
    unjudged = unjudged_total(in_week, skipped)
    per_day = daily_counts(activity, week_start)
    busiest = max(per_day, key=lambda d: d["count"]) if activity else None  # 並列時取最早的一天
    return {
        "total": len(activity),
        "complete": not unjudged,
        "unjudged": unjudged,
        "per_day": per_day,
        "active_days": sum(1 for d in per_day if d["count"] > 0),
        "busiest_day": busiest["date"] if busiest else None,
        "speed": speed_distribution(activity),
    }


def summarize_quality(records: list[Record], in_week: list[Record], skipped: Sequence[Skipped]) -> DataQuality:
    return {
        "records_in_week": len(in_week),
        "out_of_week": len(records) - len(in_week),
        "fall_field_missing": sum(1 for r in in_week if r.fall is None and r.fall_raw is None),
        "fall_unreadable": sum(1 for r in in_week if r.fall_raw is not None),
        "bad_timestamp": sum(1 for s in skipped if s.kind == "bad_timestamp"),
        "not_object": sum(1 for s in skipped if s.kind == "not_object"),
        "unjudged_total": unjudged_total(in_week, skipped),
        "skipped": [s.message for s in skipped],
    }


def build_report(
    records: list[Record], week_start: date, skipped: Sequence[Skipped] = (), title: str | None = None,
) -> WeeklyReport:
    """統計 week_start 起連續 7 天。回傳可直接序列化的 dict（欄位見 WeeklyReport）。"""
    in_week = records_in_week(records, week_start)
    out_of_week = [r for r in records if not _in_week(r, week_start)]
    return {
        "title": title,
        "week_start": week_start.isoformat(),
        "week_end": (week_start + timedelta(days=6)).isoformat(),
        "falls": summarize_falls(in_week, skipped, out_of_week),
        "activity": summarize_activity(in_week, week_start, skipped),
        "data_quality": summarize_quality(records, in_week, skipped),
        "assumptions": list(ASSUMPTIONS),
    }


# ── 輸出（只排版，不做判斷）──────────────────────────────────

def _fmt_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d:%Y/%m/%d}（{WEEKDAYS[d.weekday()]}）"


def _unjudged_parts(quality: DataQuality, falls: FallSummary, lead: str = "") -> str:
    """無法判讀的筆數，依原因分開；只列出非零的。跌倒與活動量兩節共用，兩邊說法一致。"""
    marked = falls["undated_marked_fall"]
    bad_ts = "筆時間無法解析" + (f"（其中 {marked} 筆標記為跌倒）" if marked else "") + "，可能屬於本週"
    parts = [
        (quality["fall_field_missing"], "筆沒有跌倒欄位"),
        (quality["fall_unreadable"], "筆跌倒欄位值無法辨識"),
        (quality["bad_timestamp"], bad_ts),
        (quality["not_object"], "筆不是物件（內容無法判讀），可能屬於本週"),
    ]
    return f'（{lead}{"、".join(f"{n} {what}" for n, what in parts if n)}。）'


def _fall_headline(falls: FallSummary, quality: DataQuality) -> list[str]:
    if quality["records_in_week"] == 0:
        if quality["out_of_week"]:
            lines = [(
                f'本週沒有任何紀錄，**無法判斷是否發生跌倒**；輸入資料有 {quality["out_of_week"]} 筆在本週之外，'
                "請確認週起始日是否正確。"
            )]
        else:
            lines = ["本週沒有任何紀錄，**無法判斷是否發生跌倒**（也可能是裝置沒有傳資料）。"]
        return lines + ([_unjudged_parts(quality, falls, lead="另有 ")] if quality["skipped"] else [])
    if falls["completeness"] == "none":
        return ["本週的資料沒有可判讀的跌倒欄位，**無法判斷是否發生跌倒**。", _unjudged_parts(quality, falls)]
    if falls["completeness"] == "all_readable":
        # 沒有裝置在線時段的資料（會議清單 D3），只能說「輸入的紀錄中」，不能寫成全週總數
        return [f'本次輸入的紀錄中，跌倒標記共 **{falls["count"]}** 筆。']
    return [
        f'可判讀的紀錄中偵測到 **{falls["count"]}** 次跌倒；另有 {quality["unjudged_total"]} 筆無法判讀，**全週總數無法確認**。',
        _unjudged_parts(quality, falls),
    ]


def _render_falls(falls: FallSummary, quality: DataQuality) -> list[str]:
    lines = _fall_headline(falls, quality)
    if falls["events"]:
        lines.append("")
        lines += [f'- {datetime.fromisoformat(e["at"]):%Y/%m/%d %H:%M}　原始描述：{e["motion"]}' for e in falls["events"]]
    if falls["unreadable"]:
        lines += ["", "跌倒欄位值無法辨識的紀錄（可能就是跌倒，請人工確認）："]
        lines += [
            f'- {datetime.fromisoformat(u["at"]):%Y/%m/%d %H:%M}　fall 值 {u["raw"]!r}；原始描述：{u["motion"]}'
            for u in falls["unreadable"]
        ]
    if falls["undated"]:
        lines += ["", "時間無法解析、可能屬於本週的跌倒相關紀錄（請人工確認）："]
        lines += [
            f'- 第 {u["row"]} 筆　時間 {u["timestamp"]!r}；fall 值 {u["fall"]!r}；原始描述：{u["motion"]}'
            for u in falls["undated"]
        ]
    if falls["outside_week"]:
        lines += ["", f'本週之外的跌倒紀錄 {len(falls["outside_week"])} 筆（未計入本週）：']
        lines += [f'- {datetime.fromisoformat(e["at"]):%Y/%m/%d %H:%M}　原始描述：{e["motion"]}' for e in falls["outside_week"]]
    return lines


def _activity_headline(activity: ActivitySummary, quality: DataQuality, falls: FallSummary) -> list[str]:
    if activity["complete"]:
        if activity["total"] == 0:
            return ["本次輸入的紀錄中，沒有動作紀錄（不含跌倒）。"]
        return [f'本次輸入的紀錄中，動作紀錄共 **{activity["total"]}** 筆（不含跌倒）。']
    return [
        (
            f'可判讀的紀錄中有動作紀錄 **{activity["total"]}** 筆（不含跌倒）；'
            f'另有 {quality["unjudged_total"]} 筆無法判讀、未計入，**全週總數無法確認**。'
        ),
        _unjudged_parts(quality, falls),
    ]


def _render_activity(activity: ActivitySummary, quality: DataQuality, falls: FallSummary) -> list[str]:
    lines = _activity_headline(activity, quality, falls)
    if activity["total"]:
        speed = activity["speed"]
        parts = [f"{k} {speed[k]}" for k in SPEED_ORDER] + ([f"{OTHER_SPEED} {speed[OTHER_SPEED]}"] if speed[OTHER_SPEED] else [])
        lines += [
            f'有動作紀錄的天數 {activity["active_days"]} / 7，最多的一天是 {_fmt_date(activity["busiest_day"] or "")}。',
            f'速度分布：{"、".join(parts)}。',
        ]
    lines += ["", "| 日期 | 動作紀錄筆數 |", "|---|---:|"]
    lines += [f'| {_fmt_date(d["date"])} | {d["count"]} |' for d in activity["per_day"]]
    lines += ["", DAILY_ZERO_NOTE]
    return lines


def _render_quality(assumptions: list[str], quality: DataQuality) -> list[str]:
    lines = [f"- {a}" for a in assumptions]
    lines.append(
        f'- 本週納入 {quality["records_in_week"]} 筆；週外 {quality["out_of_week"]} 筆未計；'
        f'無法解析 {len(quality["skipped"])} 筆。'
    )
    lines += [f"  - {s}" for s in quality["skipped"]]
    return lines


def render_markdown(report: WeeklyReport) -> str:
    head = f'{report["title"]} ' if report["title"] else ""
    sections = [
        [f'# {head}週報　{_fmt_date(report["week_start"])} – {_fmt_date(report["week_end"])}'],
        ["## 跌倒", "", *_render_falls(report["falls"], report["data_quality"])],
        ["## 活動量", "", *_render_activity(report["activity"], report["data_quality"], report["falls"])],
        ["## 統計口徑與資料品質", "", *_render_quality(report["assumptions"], report["data_quality"])],
        ["建議報告尚未包含在這一版。", ""],
    ]
    return "\n".join(line for section in sections for line in [*section, ""]).rstrip("\n") + "\n"


# ── 命令列 ───────────────────────────────────────────────

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
