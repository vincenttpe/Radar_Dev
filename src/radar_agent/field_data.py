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

真實資料不進 repo（REVIEW.md 第 5 節）；遮罩後的檔案仍是場域實測紀錄，只在本機使用。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

from radar_agent.caregiver import _read_records
from radar_agent.device_events import HEARTBEAT_GAP, TIMESTAMP_FORMAT

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
    gaps: list[tuple[datetime, datetime]] = field(default_factory=list)
    presence_by_hour: Counter[int] = field(default_factory=Counter)


def inspect(records: list[dict[str, Any]], layout: str) -> Report:
    report = Report(records=len(records), layout=layout)
    seen: set[str] = set()
    times: dict[str, list[datetime]] = {}
    for record in records:
        key = json.dumps(record, sort_keys=True, ensure_ascii=False)
        if key in seen:
            report.duplicates += 1
            continue
        seen.add(key)
        device = device_of(record)
        if device is None:
            report.no_topic += 1
            continue
        report.devices[device] += 1
        if str(record.get("topic", "")).count("/") > 1:
            report.commands += 1
            continue
        try:
            when = datetime.strptime(str(record["timestamp"]), TIMESTAMP_FORMAT)
        except (KeyError, ValueError):
            report.unreadable_time += 1
            continue
        times.setdefault(device, []).append(when)
        report.first = when if report.first is None else min(report.first, when)
        report.last = when if report.last is None else max(report.last, when)
        payload = record.get("payload")
        event = payload.get("event") if isinstance(payload, dict) else None
        if event == "fall" and isinstance(payload, dict):
            report.events[f"fall（{payload.get('mode')}）"] += 1
        else:
            report.events[str(event)] += 1
        if event == "presence":
            report.presence_by_hour[when.hour] += 1
    for ts in times.values():
        ts.sort()
        report.gaps += [(a, b) for a, b in pairwise(ts) if b - a > HEARTBEAT_GAP]
    report.gaps.sort()
    return report


def _span(d: timedelta) -> str:
    minutes = int(d.total_seconds() // 60)
    return f"{minutes // 60} 小時 {minutes % 60} 分" if minutes >= 60 else f"{minutes} 分 {int(d.total_seconds()) % 60} 秒"


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
            f"超過 {int(HEARTBEAT_GAP.total_seconds())} 秒沒資料：{len(r.gaps)} 段，"
            f"最長 {_span(longest[1] - longest[0])}（{longest[0]:%m-%d %H:%M:%S} 起）"
        )
    else:
        lines.append(f"超過 {int(HEARTBEAT_GAP.total_seconds())} 秒沒資料：沒有")
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
            try:
                when = datetime.strptime(str(masked["timestamp"]), TIMESTAMP_FORMAT)
                masked["timestamp"] = (when + shift).strftime(TIMESTAMP_FORMAT)
            except (KeyError, ValueError):
                unshifted += 1
        out.setdefault(alias, []).append(masked)
    return out, unshifted


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
    ap = argparse.ArgumentParser(description="實測資料的檢查（inspect）與裝置識別遮罩（mask）")
    sub = ap.add_subparsers(dest="command", required=True)
    p_inspect = sub.add_parser("inspect", help="格式、時間範圍、事件數、斷線與每小時分布")
    p_inspect.add_argument("events", type=Path)
    p_mask = sub.add_parser("mask", help="裝置識別碼換成代號，每台裝置寫成一個檔案")
    p_mask.add_argument("events", type=Path)
    p_mask.add_argument("--out-dir", type=Path, required=True, help=f"輸出目錄；在 repo 裡時只能是 {OUTPUTS_DIR}/ 底下")
    p_mask.add_argument("--shift-hours", type=int, default=0, help="時間戳平移的小時數（A27 確定是 UTC 時用 8）")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    try:
        records = _read_records(args.events)
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
