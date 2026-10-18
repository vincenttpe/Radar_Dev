"""輸出檢查：給讀者的文字送出前都要通過，沒通過就不輸出（或退回範本）。

範本、句庫與在地模型的改寫都用同一套檢查（rewrite.py、care_record.py、caregiver.py 的命令列）。
檢查的是文字本身，不信任產生文字的程式：允許的數字從事實重新算一次，不沿用範本算好的結果。
用詞限制來自非醫療器材定位（docs/adr/0008）與 C2（語音回覆沒事不直接算誤報）。
改這裡屬單向門「給讀者的輸出與用語」（REVIEW.md 第 2 節）。
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from radar_agent.device_events import Summary
from radar_agent.kg_rules import Assessment

FORBIDDEN = {
    "診斷": "非醫療器材", "治療": "非醫療器材", "處方": "非醫療器材", "保證": "非醫療器材",
    "誤報": "C2：語音回覆沒事不直接算誤報",
}
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def numbers(text: str) -> set[Decimal]:
    return {Decimal(n) for n in _NUMBER.findall(text)}


def _time_numbers(when: datetime) -> set[Decimal]:
    return {Decimal(when.hour), Decimal(when.minute)}


def _facts(source: Summary | Assessment, place: str) -> set[Decimal]:
    """事實裡出現的所有數字：計數、時間（時、分）、日期、停留時間，或知識圖譜的原文。"""
    allowed = numbers(place)
    if isinstance(source, Summary):
        counts = [
            len(source.visits), len(source.fall_alerts), sum(a.distress_call for a in source.fall_alerts),
            sum(not a.distress_call for a in source.fall_alerts),
            len(source.no_need_help_replies), len(source.longstay_alerts), len(source.interrupted_visits),
            len(source.errors), len(source.unreadable) + len(source.unknown_events),
        ]
        allowed |= {Decimal(c) for c in counts}
        times = [a.time for a in source.fall_alerts] + source.longstay_alerts + [w for w, _ in source.errors]
        times += [r.time for r in source.resolutions]
        allowed |= {Decimal(a.delay_s) for a in source.fall_alerts if a.delay_s is not None}
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
            allowed |= numbers(str(source[key]))
    if source["score"] is not None:
        allowed.add(Decimal(source["score"]))
    allowed |= numbers(source["kg"]["version"])
    return allowed


def wording_problems(text: str) -> list[str]:
    problems = []
    for word, why in FORBIDDEN.items():
        if word in text:
            problems.append(f"不能出現「{word}」（{why}）" if why == "非醫療器材" else f"不能寫「{word}」（{why}）")
    return problems


def check_output(text: str, source: Summary | Assessment, *, place: str = "") -> list[str]:
    """回報輸出文字的問題；空清單表示通過。"""
    problems = []
    allowed = _facts(source, place)
    for number in sorted(numbers(text) - allowed):
        problems.append(f"數字 {number} 不在事實裡")
    unquoted = text
    if not isinstance(source, Summary):
        # 知識圖譜原文另有單向門與審核人把關，照搬的部分不檢查用詞
        for key in ("suggestion", "recommended_dose", "source_text", "rank"):
            if source[key]:
                unquoted = unquoted.replace(str(source[key]), "")
    problems += wording_problems(unquoted)
    if isinstance(source, Summary):
        disconnected = len(source.online) != 1 or bool(source.interrupted_visits)
        for label in ("跌倒警報", "主動呼救"):
            if disconnected and re.search(rf"(?<!在線期間){label} 0 次", text):
                problems.append(f"裝置有斷線，不能寫「{label} 0 次」而不註明在線期間")
    return problems
