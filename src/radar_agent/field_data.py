"""實測資料的檢查與遮罩（docs/11「10/19 實測資料」）。

radar-field-data inspect 紀錄檔
    看一份雲端紀錄檔的格式、時間範圍、事件數、斷線段數與每小時有人的次數。
    裝置識別碼像 MAC 時不印出原值。每小時的分布用來檢查時區（會議清單 A27）：
    浴廁的使用若集中在半夜，時間戳很可能差了 8 小時。

radar-field-data mask 紀錄檔 --out-dir outputs/field [--shift-hours 8]
    把裝置識別碼換成代號 FIELD-01、FIELD-02……，每台裝置寫成一個檔案；
    紀錄裡其他像 MAC 的字串一律換成 MASKED-ID。--shift-hours 把時間戳平移整數小時
    （A27 確定時間戳是 UTC 時用 8）。只能寫進 repo 的 outputs/（已被 git 忽略）或 repo 以外的地方，
    避免實測資料被 commit。代號與原識別碼的對照不寫檔、不印出。

radar-field-data sheet > 操演紀錄.csv
radar-field-data compare 操演紀錄.csv outputs/field/FIELD-01.jsonl
    10/19 實機操演（docs/12「10/19 實機操演」）：sheet 印出空白紀錄表，現場填每一項開始時的手錶時間；
    compare 依紀錄表把資料切成一段一段（這一項開始到下一項開始），逐項比對預期的裝置事件，
    列出時間差與實際產生的處置提示。資料時間戳與手錶差幾小時（0、+8、−8 都試）也一併判斷（A27）。

真實資料不進 repo（REVIEW.md 第 5 節）；遮罩後的檔案仍是場域實測紀錄，只在本機使用。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

from radar_agent.caregiver import event_notes
from radar_agent.device_events import (
    HEARTBEAT_GAP,
    TIMESTAMP_FORMAT,
    Summary,
    parse_timestamp,
    read_records,
    summarize,
    timeline,
)

ALIAS_PREFIX = "FIELD-"
MASKED = "MASKED-ID"
# 冒號或連字號分隔的 MAC，或連續 12 碼十六進位
MAC_LIKE = re.compile(r"(?i)\b[0-9a-f]{2}(?:[:-][0-9a-f]{2}){5}\b|\b[0-9a-f]{12}\b")
OUTPUTS_DIR = "outputs"


def device_of(record: dict[str, Any]) -> str | None:
    """topic 的第二段（radar10/<裝置>/…）；沒有就回傳 None。"""
    parts = str(record.get("topic", "")).split("/")
    return parts[1] if len(parts) >= 2 and parts[1] else None


def shown(device: str) -> str:
    """印給人看的裝置名稱：像 MAC 就不印原值。"""
    return "（識別碼像 MAC，不顯示）" if MAC_LIKE.search(device) else device


# ── inspect ─────────────────────────────────────────────

@dataclass
class Report:
    records: int = 0
    layout: str = ""
    devices: Counter[str] = field(default_factory=Counter)
    no_topic: int = 0
    commands: int = 0                                   # topic 多層：伺服器下給裝置的指令
    unreadable_time: int = 0
    duplicates: int = 0
    first: datetime | None = None
    last: datetime | None = None
    events: Counter[str] = field(default_factory=Counter)
    gaps: list[tuple[datetime, datetime]] = field(default_factory=list)  # 斷線：前一段在線時段結束到下一段開始
    ended_offline: list[datetime] = field(default_factory=list)         # 資料結束前就送出 offline 的時間
    presence_by_hour: Counter[int] = field(default_factory=Counter)


def inspect(records: list[dict[str, Any]], layout: str) -> Report:
    report = Report(records=len(records), layout=layout)
    line = timeline(records)
    report.duplicates = line.duplicates
    for record in [r for _, r in line.no_topic + line.commands + line.unreadable_time] + [e.record for e in line.entries]:
        device = device_of(record)
        if device is None:
            report.no_topic += 1
        else:
            report.devices[device] += 1
    report.commands = sum(device_of(r) is not None for _, r in line.commands)
    report.unreadable_time = sum(device_of(r) is not None for _, r in line.unreadable_time)
    by_topic: dict[str, list[dict[str, Any]]] = {}
    for entry in line.entries:
        if device_of(entry.record) is None:
            continue
        when, payload = entry.when, entry.payload
        by_topic.setdefault(entry.topic, []).append(entry.record)
        report.first = when if report.first is None else min(report.first, when)
        report.last = when if report.last is None else max(report.last, when)
        event = payload.get("event") if isinstance(payload, dict) else None
        if event == "fall" and isinstance(payload, dict):
            report.events[f"fall（{payload.get('mode')}）"] += 1
        else:
            report.events[str(event)] += 1
        if event == "presence":
            report.presence_by_hour[when.hour] += 1
    # 斷線和 radar-caregiver 用同一個定義：在線時段之間的空檔（超過 HEARTBEAT_GAP 沒有可判讀的資料，或裝置送出 offline）
    for device_records in by_topic.values():
        summary = summarize(device_records)
        report.gaps += [(a.end, b.start) for a, b in pairwise(summary.online)]
        if summary.ended_offline is not None:
            report.ended_offline.append(summary.ended_offline)
    report.gaps.sort()
    report.ended_offline.sort()
    return report


def _span(d: timedelta) -> str:
    minutes = int(d.total_seconds() // 60)
    return f"{minutes // 60} 小時 {minutes % 60} 分" if minutes >= 60 else f"{minutes} 分 {int(d.total_seconds()) % 60} 秒"


def _gap_label() -> str:
    """有收到但 payload 讀不懂的紀錄不算心跳（和 radar-caregiver 一樣，那段寫「無法判斷」），所以寫「可判讀」。"""
    return f"斷線（超過 {int(HEARTBEAT_GAP.total_seconds())} 秒沒有可判讀的資料，或 offline）"


def report_text(r: Report) -> str:
    lines = [f"格式：{r.layout}，{r.records:,} 筆（完全相同的重複 {r.duplicates:,} 筆）"]
    names = "、".join(f"{shown(d)} {n:,} 筆" for d, n in r.devices.most_common())
    lines.append(f"裝置：{len(r.devices)} 台" + (f"（{names}）" if names else ""))
    if any(MAC_LIKE.search(d) for d in r.devices):
        lines.append("  識別碼像 MAC：展示或給別人之前先用 radar-field-data mask 遮罩")
    if len(r.devices) > 1:
        lines.append("  radar-caregiver 一次只整理一台；mask 會把每台裝置分成一個檔案")
    if r.no_topic:
        lines.append(f"沒有 topic：{r.no_topic:,} 筆")
    if r.commands:
        lines.append(f"伺服器下給裝置的指令（topic 多層）：{r.commands:,} 筆，不算進事件")
    if r.unreadable_time:
        lines.append(f"時間戳讀不懂（不是 {TIMESTAMP_FORMAT}）：{r.unreadable_time:,} 筆")
    if r.first is None or r.last is None:
        lines.append("時間範圍：沒有讀得懂的時間戳")
        return "\n".join(lines)
    lines.append(f"時間範圍：{r.first:%Y-%m-%d %H:%M:%S} ～ {r.last:%Y-%m-%d %H:%M:%S}（時間戳沒有時區，照原樣）")
    lines.append("事件：" + "、".join(f"{e} {n:,}" for e, n in sorted(r.events.items())))
    if r.gaps:
        longest = max(r.gaps, key=lambda g: g[1] - g[0])
        lines.append(
            f"{_gap_label()}：{len(r.gaps)} 段，"
            f"最長 {_span(longest[1] - longest[0])}（{longest[0]:%m-%d %H:%M:%S} 起）"
        )
    else:
        lines.append(f"{_gap_label()}：沒有")
    for t in r.ended_offline:
        lines.append(f"  {t:%m-%d %H:%M:%S} 送出 offline 之後就沒有資料，之後的狀況不明")
    hours = " ".join(f"{h:02d}:{r.presence_by_hour[h]}" for h in range(24))
    lines.append(f"每小時有人（presence）次數：{hours}")
    lines.append("  檢查時區（A27）：浴廁使用若集中在 00～06 點，時間戳可能差了 8 小時")
    return "\n".join(lines)


# ── mask ────────────────────────────────────────────────

def mask(records: list[dict[str, Any]], *, shift_hours: int = 0) -> tuple[dict[str, list[dict[str, Any]]], int]:
    """回傳（{代號: 該裝置遮罩後的紀錄}, 時間讀不懂、沒有平移的筆數）。代號依裝置第一次出現的順序。

    沒有 topic 的紀錄分不到裝置，捨棄並由呼叫端回報。
    """
    aliases: dict[str, str] = {}
    out: dict[str, list[dict[str, Any]]] = {}
    unshifted = 0
    shift = timedelta(hours=shift_hours)
    for record in records:
        device = device_of(record)
        if device is None:
            continue
        alias = aliases.setdefault(device, f"{ALIAS_PREFIX}{len(aliases) + 1:02d}")
        parts = str(record["topic"]).split("/")
        text = json.dumps({**record, "topic": "/".join([parts[0], alias, *parts[2:]])}, ensure_ascii=False)
        if len(device) >= 8:  # 識別碼夠長才在其他欄位裡替換，免得把短的代號換到不相干的文字
            text = text.replace(device, alias)
        masked: dict[str, Any] = json.loads(MAC_LIKE.sub(MASKED, text))
        if shift:
            when = parse_timestamp(masked.get("timestamp"))
            if when is None:
                unshifted += 1
            else:
                masked["timestamp"] = (when + shift).strftime(TIMESTAMP_FORMAT)
        out.setdefault(alias, []).append(masked)
    return out, unshifted


# ── compare：實機操演紀錄表與資料逐項比對 ───────────────────
# 狀況照 docs/12「操演腳本與紀錄表」的 0～9 項。預期只寫裝置事件規格（data/samples_device_event/README.md）
# 裡有的組合；對不上時列出實際收到的，不改預期去湊（docs/12「操演後」第 3 點）。

@dataclass(frozen=True)
class Scenario:
    name: str
    kind: str                                   # sync、visit、fall、longstay、offline
    falls: tuple[tuple[str, str], ...] = ()     # 預期的跌倒警報（mode, trigger），依序
    resolved: tuple[str, ...] = ()              # 預期出現的解除方式（依序，可以有其他的）


SCENARIOS: dict[str, Scenario] = {
    "0": Scenario("開始對時", "sync"),
    "1": Scenario("一般使用", "visit"),
    "2": Scenario("A：疑似跌倒、無回應、回覆沒事", "fall", (("radar", "timeout"),), ("noNeedHelp",)),
    "3": Scenario("B：疑似跌倒、回覆需要協助", "fall", (("fusion", "conversation"),), ("standUp",)),
    "4": Scenario("C：主動呼救", "fall", (("speech", "conversation"),), ("standUp",)),
    "5": Scenario("C'：主動呼救後無回應", "fall", (("speech", "timeout"),)),
    "6": Scenario("跌倒後一直沒起身", "fall", (("radar", "timeout"),), ("standUp",)),
    "7": Scenario("D：久待警報", "longstay"),
    "8": Scenario("斷線", "offline"),
    "9": Scenario("結束對時", "sync"),
}
SHEET_HEADER = ["項目", "狀況", "手錶時間", "備註"]
SYNC_TOLERANCE = timedelta(seconds=60)   # 對時：手錶時間前後 1 分鐘內要有資料
LEAD = timedelta(seconds=60)             # 每一段從手錶時間前 1 分鐘開始算，容許時鐘差與手錶記得晚
LAST_WINDOW = timedelta(minutes=30)      # 最後一項往後看多久
OFFSETS = (0, 8, -8)                     # 資料時間戳減手錶時間，試這幾種小時差


def sheet_text() -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(SHEET_HEADER)
    for k, sc in SCENARIOS.items():
        w.writerow([k, sc.name, "", ""])
    return out.getvalue()


@dataclass(frozen=True)
class Step:
    item: str
    at: datetime | None   # None：這一項沒有操演
    note: str


def read_sheet(text: str, day: date | None) -> list[Step]:
    """手錶時間寫「2026-10-19 10:03:15」，或只寫「10:03:15」並給 day。認不得的項目或時間就整份拒收。"""
    rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
    if not rows or rows[0][:3] != SHEET_HEADER[:3]:
        raise ValueError(f"第一行要是 {','.join(SHEET_HEADER)}（用 radar-field-data sheet 產生）")
    steps: list[Step] = []
    for n, row in enumerate(rows[1:], 2):
        if not any(c.strip() for c in row):
            continue
        item, when = row[0].strip(), (row[2].strip() if len(row) > 2 else "")
        if item not in SCENARIOS:
            raise ValueError(f"第 {n} 行：項目「{item}」不在 0～9 之內")
        at = None
        if when:
            try:
                at = datetime.strptime(when, TIMESTAMP_FORMAT)
            except ValueError:
                try:
                    clock = datetime.strptime(when, "%H:%M:%S").time()
                except ValueError as e:
                    raise ValueError(f"第 {n} 行：手錶時間「{when}」要寫成 2026-10-19 10:03:15 或 10:03:15") from e
                if day is None:
                    raise ValueError(f"第 {n} 行只寫了時間，請用 --date 指定日期") from None
                at = datetime.combine(day, clock)
        steps.append(Step(item, at, row[3].strip() if len(row) > 3 else ""))
    timed = [s.at for s in steps if s.at is not None]
    if any(b <= a for a, b in pairwise(timed)):
        raise ValueError("手錶時間要照操演順序由早到晚")
    return steps


@dataclass
class Check:
    step: Step
    scenario: Scenario
    status: str = "沒有操演"            # 符合、不符、找不到資料、沒有操演
    found_at: datetime | None = None   # 這一項第一個相關事件的資料時間
    expected: str = ""
    actual: str = ""
    prompts: list[str] = field(default_factory=list)
    extra: str = ""


def _fall_label(mode: str | None, trigger: str | None) -> str:
    return f"跌倒警報（{mode or '來源不明'}／{trigger or '?'}）"


def _describe(summary: Summary, raw_falls: int) -> str:
    parts = [_fall_label(a.mode, a.trigger) for a in summary.fall_alerts]
    parts += [f"解除（{r.trigger or '?'}）" for r in summary.resolutions]
    if summary.longstay_alerts:
        parts.append(f"久待警報 {len(summary.longstay_alerts)} 次")
    visits = len(summary.visits) + len(summary.interrupted_visits) + (summary.ongoing_visit is not None)
    if visits:
        parts.append(f"有人使用 {visits} 次")
    if len(summary.online) > 1:
        parts.append(f"斷線 {len(summary.online) - 1} 段")
    if raw_falls > len(summary.fall_alerts):
        parts.append(f"原始 fall 訊息 {raw_falls} 筆")
    return "、".join(parts) or "沒有任何事件"


def _expected(sc: Scenario) -> str:
    if sc.kind == "sync":
        return f"手錶時間前後 {int(SYNC_TOLERANCE.total_seconds())} 秒內有資料"
    if sc.kind == "visit":
        return "有人使用，沒有跌倒或久待警報"
    if sc.kind == "longstay":
        return "久待警報"
    if sc.kind == "offline":
        return "斷線（超過 30 秒沒資料或 offline）"
    text = " → ".join(_fall_label(m, t) for m, t in sc.falls)
    if sc.resolved:
        text += " → " + " → ".join(f"解除（{r}）" for r in sc.resolved)
    return text


def _in_order(expected: tuple[str, ...], actual: list[str | None]) -> bool:
    it = iter(actual)
    return all(any(a == e for a in it) for e in expected)


def _window(timed: list[tuple[datetime, dict[str, Any]]], start: datetime, end: datetime) -> list[dict[str, Any]]:
    return [r for t, r in timed if start <= t < end]


def check_step(step: Step, nxt: datetime | None, timed: list[tuple[datetime, dict[str, Any]]], offset: timedelta) -> Check:
    sc = SCENARIOS[step.item]
    c = Check(step, sc, expected=_expected(sc))
    if step.at is None:
        return c
    at = step.at + offset
    if sc.kind == "sync":
        near = [t for t, _ in timed if abs(t - at) <= SYNC_TOLERANCE]
        if near:
            c.found_at = min(near, key=lambda t: abs(t - at))
            c.status, c.actual = "符合", "有資料"
        else:
            c.status, c.actual = "找不到資料", "前後沒有資料"
        return c
    records = _window(timed, at - LEAD, (nxt + offset - LEAD) if nxt else at + LAST_WINDOW)
    if not records:
        c.status, c.actual = "找不到資料", "這一段沒有資料"
        return c
    summary = summarize(records)
    raw_falls = sum(1 for r in records if isinstance(r.get("payload"), dict) and r["payload"].get("event") == "fall")
    c.actual = _describe(summary, raw_falls)
    c.prompts = [f"{n.span or f'{n.time:%H:%M}'} {n.prompt}" for n in event_notes(summary, "")]
    if sc.kind == "visit":
        starts = [v.start for v in summary.visits + summary.interrupted_visits]
        if summary.ongoing_visit is not None:
            starts.append(summary.ongoing_visit.start)
        c.found_at = min(starts) if starts else None
        ok = bool(starts) and not summary.fall_alerts and not summary.longstay_alerts
    elif sc.kind == "longstay":
        c.found_at = summary.longstay_alerts[0] if summary.longstay_alerts else None
        ok = c.found_at is not None
    elif sc.kind == "offline":
        c.found_at = summary.online[0].end if len(summary.online) > 1 else None
        ok = c.found_at is not None
    else:
        c.found_at = summary.fall_alerts[0].time if summary.fall_alerts else None
        got = tuple((a.mode or "", a.trigger or "") for a in summary.fall_alerts)
        ok = got == sc.falls and _in_order(sc.resolved, [r.trigger for r in summary.resolutions])
        if step.item == "6" and summary.fall_alerts:
            c.extra = f"原始 fall 訊息 {raw_falls} 筆，合併成 {len(summary.fall_alerts)} 則跌倒警報"
    c.status = "符合" if ok else "不符"   # 這一段有資料但沒有預期的事件，也是不符（實際收到什麼列在 actual）
    return c


@dataclass
class Comparison:
    offset_hours: int
    checks: list[Check]


def compare(steps: list[Step], records: list[dict[str, Any]]) -> Comparison:
    """依紀錄表逐項比對；資料時間戳與手錶差幾小時，取對得上最多項的那一種（同分時取 0）。"""
    timed: list[tuple[datetime, dict[str, Any]]] = []
    for r in records:
        when = parse_timestamp(r.get("timestamp"))
        if when is not None:
            timed.append((when, r))
    timed.sort(key=lambda x: x[0])
    nexts = [next((s.at for s in steps[i + 1:] if s.at is not None), None) for i in range(len(steps))]
    best: Comparison | None = None
    for hours in OFFSETS:
        offset = timedelta(hours=hours)
        checks = [check_step(s, n, timed, offset) for s, n in zip(steps, nexts, strict=True)]
        result = Comparison(hours, checks)
        score = sum(c.found_at is not None for c in checks)
        if best is None or score > sum(c.found_at is not None for c in best.checks):
            best = result
    assert best is not None
    return best


def _delay(c: Check, offset_hours: int) -> timedelta | None:
    if c.found_at is None or c.step.at is None:
        return None
    return c.found_at - (c.step.at + timedelta(hours=offset_hours))


def _signed(d: timedelta) -> str:
    sec = int(d.total_seconds())
    return f"{'+' if sec >= 0 else '−'}{abs(sec)} 秒"


def comparison_text(result: Comparison) -> str:
    checks = result.checks
    timed = [c for c in checks if c.step.at is not None]
    lines = [f"操演紀錄 {len(checks)} 項，{len(timed)} 項有手錶時間。"]
    if result.offset_hours:
        lines.append(
            f"資料時間戳比手錶{'晚' if result.offset_hours > 0 else '早'} {abs(result.offset_hours)} 小時才對得上"
            f"（試過 0、+8、−8 小時）。若資料是 UTC，遮罩時加 --shift-hours {-result.offset_hours}（A27）。"
        )
    else:
        lines.append("資料時間戳與手錶同一個時區（試過 0、+8、−8 小時，0 對得上最多項）。")
    for c in checks:
        when = f"{c.step.at:%H:%M:%S}" if c.step.at else "—"
        got = f"{c.found_at:%H:%M:%S}" if c.found_at else "—"
        d = _delay(c, result.offset_hours)
        lines.append("")
        lines.append(f"[{c.status}] {c.step.item} {c.scenario.name}　手錶 {when}　資料 {got}" + (f"（{_signed(d)}）" if d is not None else ""))
        if c.status == "沒有操演":
            continue
        lines.append(f"  預期：{c.expected}")
        lines.append(f"  實際：{c.actual}")
        if c.extra:
            lines.append(f"  {c.extra}")
        for prompt in c.prompts:
            lines.append(f"  處置提示：{prompt}")
        if c.step.note:
            lines.append(f"  備註：{c.step.note}")
    counts = Counter(c.status for c in checks)
    lines.append("")
    lines.append("結果：" + "、".join(f"{k} {counts[k]}" for k in ("符合", "不符", "找不到資料", "沒有操演") if counts[k]))
    # 久待警報要等 delaysec 才送出，這段等待不是時鐘差，不列入中位數
    delays = [d.total_seconds() for c in checks
              if c.scenario.kind != "longstay" and (d := _delay(c, result.offset_hours)) is not None]
    if delays:
        lines.append(
            f"時間差中位數 {_signed(timedelta(seconds=statistics.median(delays)))}（不含久待警報；"
            "是手錶與資料時鐘的差，加上動作到裝置判定、傳送的時間；KPI 3 要的是發生到通知，這裡量不到）"
        )
    lines.append("不符或找不到資料的項目，請記進會議清單，不要改預期去湊（docs/12「操演後」）。")
    return "\n".join(lines)


def repo_root(path: Path) -> Path | None:
    """path 所在的 git repo 根目錄（往上找 .git）；不在 repo 裡回傳 None。"""
    for parent in [path, *path.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def check_out_dir(out_dir: Path) -> str | None:
    """實測資料只能寫到 repo 的 outputs/ 或 repo 以外；回傳錯誤訊息或 None。"""
    target = out_dir.resolve()
    root = repo_root(target)
    if root is not None and not target.is_relative_to(root / OUTPUTS_DIR):
        return f"{out_dir} 在 repo 裡、但不在 {OUTPUTS_DIR}/ 底下；實測資料不能放進會被 commit 的地方"
    return None


def _layout(path: Path) -> str:
    return "JSON 陣列" if path.read_text(encoding="utf-8").lstrip().startswith("[") else "一行一筆（JSON Lines）"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="實測資料的檢查（inspect）、裝置識別遮罩（mask）與實機操演比對（sheet、compare）")
    sub = ap.add_subparsers(dest="command", required=True)
    p_inspect = sub.add_parser("inspect", help="格式、時間範圍、事件數、斷線與每小時分布")
    p_inspect.add_argument("events", type=Path)
    p_mask = sub.add_parser("mask", help="裝置識別碼換成代號，每台裝置寫成一個檔案")
    p_mask.add_argument("events", type=Path)
    p_mask.add_argument("--out-dir", type=Path, required=True, help=f"輸出目錄；在 repo 裡時只能是 {OUTPUTS_DIR}/ 底下")
    p_mask.add_argument("--shift-hours", type=int, default=0, help="時間戳平移的小時數（A27 確定是 UTC 時用 8）")
    sub.add_parser("sheet", help="印出空白的實機操演紀錄表（CSV）")
    p_cmp = sub.add_parser("compare", help="實機操演紀錄表與資料逐項比對")
    p_cmp.add_argument("sheet", type=Path, help="填好手錶時間的操演紀錄表（radar-field-data sheet 產生）")
    p_cmp.add_argument("events", type=Path, help="同一段時間的資料（建議先 mask）；一台裝置")
    p_cmp.add_argument("--date", type=date.fromisoformat, help="紀錄表只寫時間時的日期，例如 2026-10-19")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    if args.command == "sheet":
        print(sheet_text(), end="")
        return 0
    if args.command == "compare":
        try:
            steps = read_sheet(args.sheet.read_text(encoding="utf-8"), args.date)
            result = compare(steps, read_records(args.events))
        except (OSError, ValueError) as e:
            print(f"錯誤：{e}", file=sys.stderr)
            return 2
        print(comparison_text(result))
        return 0 if all(c.status in ("符合", "沒有操演") for c in result.checks) else 1

    try:
        records = read_records(args.events)
        layout = _layout(args.events)
    except (OSError, ValueError) as e:
        print(f"錯誤：{args.events}：{e}", file=sys.stderr)
        return 2

    if args.command == "inspect":
        print(report_text(inspect(records, layout)))
        return 0

    problem = check_out_dir(args.out_dir)
    if problem:
        print(f"錯誤：{problem}", file=sys.stderr)
        return 2
    by_device, unshifted = mask(records, shift_hours=args.shift_hours)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for alias, rs in by_device.items():
        path = args.out_dir / f"{alias}.jsonl"
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rs), encoding="utf-8")
        print(f"寫入 {path}（{len(rs):,} 筆）")
    dropped = len(records) - sum(len(rs) for rs in by_device.values())
    if dropped:
        print(f"沒有 topic、分不到裝置而捨棄：{dropped:,} 筆")
    if args.shift_hours:
        print(f"時間戳已平移 {args.shift_hours:+d} 小時" + (f"；{unshifted:,} 筆時間讀不懂，沒有平移" if unshifted else ""))
    print("代號與原識別碼的對照沒有存下；遮罩後的檔案仍是實測紀錄，不要 commit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
