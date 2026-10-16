"""居服員 Profile：把事實（場域事件摘要、知識圖譜等級）套進固定範本，並檢查輸出。

docs/02「在地模型負載與分流」的範本層：範本是主要路徑，不經過模型。
原則 2：事實在上游決定；這裡只補前後句子，知識圖譜的等級與建議原文照搬。
用語於 2026-10-14 在對話中確認；改範本用語屬單向門「給讀者的輸出與用語」（REVIEW.md 第 2 節）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from radar_agent.device_events import Summary, summarize
from radar_agent.kg_rules import Assessment


def _duration(span: timedelta) -> str:
    minutes, seconds = divmod(int(span.total_seconds()), 60)
    if minutes and seconds:
        return f"{minutes} 分 {seconds} 秒"
    if minutes:
        return f"{minutes} 分鐘"
    return f"{seconds} 秒"


def _site_text(summary: Summary, place: str) -> str:
    if not summary.online:
        return f"{place}：沒有收到資料，無法判斷。"
    first, last = summary.online[0].start, summary.online[-1].end
    days = f"{first.month}/{first.day}"
    if last.date() != first.date():
        days += f"–{last.month}/{last.day}"
    parts = [f"{place} {days}："]
    if summary.visits:
        longest = max(v.duration for v in summary.visits)
        parts.append(f"有人使用 {len(summary.visits)} 次，最長一次 {_duration(longest)}。")
    else:
        parts.append("沒有偵測到有人使用。")
    if summary.interrupted_visits:
        parts.append(f"另有 {len(summary.interrupted_visits)} 次使用途中裝置斷線，停留時間不明。")
    if summary.ongoing_visit is not None:
        parts.append(f"資料結束時仍有人在內（{summary.ongoing_visit.start:%H:%M} 起）。")
    gaps = [(a.end, b.start) for a, b in zip(summary.online, summary.online[1:], strict=False)]
    scope = "在線期間" if gaps else ""
    falls = summary.fall_alerts
    detail = ""
    if falls:
        detail = "、".join(f"{a.time:%H:%M}" for a in falls)
        distress = sum(a.distress_call for a in falls)
        if distress:
            detail += f"；含主動呼救 {distress} 次"
        detail = f"（{detail}）"
    parts.append(f"{scope}跌倒警報 {len(falls)} 次{detail}。")
    if summary.no_need_help_replies:
        parts.append(f"語音回覆沒事 {len(summary.no_need_help_replies)} 次。")
    stays = summary.longstay_alerts
    times = f"（{'、'.join(f'{t:%H:%M}' for t in stays)}）" if stays else ""
    parts.append(f"{scope}久待警報 {len(stays)} 次{times}。")
    if gaps:
        for off, on in gaps:
            parts.append(f"{off:%H:%M}–{on:%H:%M} 裝置斷線，這段時間無法判斷是否有跌倒。")
    else:
        parts.append("這段時間裝置都在線。")
    if summary.errors:
        times = "、".join(f"{when:%H:%M}" for when, _ in summary.errors)
        parts.append(f"裝置回報錯誤 {len(summary.errors)} 次（{times}），請通知資訊人員。")
    unreadable = len(summary.unreadable) + len(summary.unknown_events)
    if unreadable:
        parts.append(f"另有 {unreadable} 筆紀錄無法判讀，請通知資訊人員。")
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



# ── 輸出檢查 ─────────────────────────────────────────────
# 範本與日後在地模型的改寫都要通過；檢查的是文字本身，不信任產生文字的程式。

FORBIDDEN = {
    "診斷": "非醫療器材", "治療": "非醫療器材", "處方": "非醫療器材", "保證": "非醫療器材",
    "誤報": "C2：語音回覆沒事不直接算誤報",
}
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _numbers(text: str) -> set[Decimal]:
    return {Decimal(n) for n in _NUMBER.findall(text)}


def _time_numbers(when: datetime) -> set[Decimal]:
    return {Decimal(when.hour), Decimal(when.minute)}


def _facts(source: Summary | Assessment, place: str) -> set[Decimal]:
    """事實裡出現的所有數字：計數、時間（時、分）、日期、停留時間，或知識圖譜的原文。"""
    allowed = _numbers(place)
    if isinstance(source, Summary):
        counts = [
            len(source.visits), len(source.fall_alerts), sum(a.distress_call for a in source.fall_alerts),
            len(source.no_need_help_replies), len(source.longstay_alerts), len(source.interrupted_visits),
            len(source.errors), len(source.unreadable) + len(source.unknown_events),
        ]
        allowed |= {Decimal(c) for c in counts}
        times = [a.time for a in source.fall_alerts] + source.longstay_alerts + [w for w, _ in source.errors]
        times += [p.start for p in source.online] + [p.end for p in source.online]
        if source.ongoing_visit is not None:
            times.append(source.ongoing_visit.start)
        for when in times:
            allowed |= _time_numbers(when) | {Decimal(when.month), Decimal(when.day)}
        for visit in source.visits:
            allowed |= {Decimal(n) for n in divmod(int(visit.duration.total_seconds()), 60)}
        return allowed
    for key in ("value", "unit", "rank", "source_text", "suggestion", "recommended_dose"):
        if source[key] is not None:
            allowed |= _numbers(str(source[key]))
    if source["score"] is not None:
        allowed.add(Decimal(source["score"]))
    allowed |= _numbers(source["kg"]["version"])
    return allowed


def check_output(text: str, source: Summary | Assessment, *, place: str = "") -> list[str]:
    """回報輸出文字的問題；空清單表示通過。"""
    problems = []
    allowed = _facts(source, place)
    for number in sorted(_numbers(text) - allowed):
        problems.append(f"數字 {number} 不在事實裡")
    unquoted = text
    if not isinstance(source, Summary):
        # 知識圖譜原文另有單向門與審核人把關，照搬的部分不檢查用詞
        for key in ("suggestion", "recommended_dose", "source_text", "rank"):
            if source[key]:
                unquoted = unquoted.replace(str(source[key]), "")
    for word, why in FORBIDDEN.items():
        if word in unquoted:
            problems.append(f"不能出現「{word}」（{why}）" if why == "非醫療器材" else f"不能寫「{word}」（{why}）")
    if isinstance(source, Summary):
        disconnected = len(source.online) != 1 or bool(source.interrupted_visits)
        if disconnected and re.search(r"(?<!在線期間)跌倒警報 0 次", text):
            problems.append("裝置有斷線，不能寫「跌倒警報 0 次」而不註明在線期間")
    return problems


# ── 命令列 ───────────────────────────────────────────────
# 裝置事件檔 → summarize → caregiver_text → check_output；檢查沒通過就不輸出文字。

def _read_records(path: Path) -> list[dict[str, Any]]:
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="一台裝置的事件 → 居服員看的場域摘要（範本，不用模型；先過輸出檢查）")
    ap.add_argument("events", type=Path, help="紀錄檔：雲端格式 {timestamp, topic, resolution, payload}，JSON 陣列或一行一筆")
    ap.add_argument("--place", required=True, help="地點名稱，例如「1 號廁所」；不放長者姓名等個人資料")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)

    if not args.place.strip():
        print("錯誤：--place 不能是空白", file=sys.stderr)
        return 2
    try:
        summary = summarize(_read_records(args.events))
    except (OSError, ValueError) as e:
        print(f"錯誤：{args.events}：{e}", file=sys.stderr)
        return 2
    text = caregiver_text(summary, place=args.place)
    problems = check_output(text, summary, place=args.place)
    if problems:
        print("輸出檢查沒通過，不輸出給居服員：", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
