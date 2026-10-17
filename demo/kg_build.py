"""產生知識圖譜 Demo 頁 demo/kg_demo.html（**模擬資料**，內部確認 Demo 功能用；docs/11「知識圖譜 Demo」）。

一位長者一週的模擬姿態與活動事件 → 換算規則（**草案**）→ 量測值 → 知識圖譜門檻（radar-kg-level 的
`assess`）→ 居服員看的等級與建議（`rewrite()`，目前是範本）→ 幾個固定問題的回答，每個回答都附依據
（規則版本、知識圖譜版本、事件編號）。

為什麼是模擬：10/19 的實測資料只有浴廁裝置事件（進出、久待、跌倒、呼救、斷線），換算不出知識圖譜的
五項量測值（docs/02「對應怎麼做」）。這裡的事件照 schemas/event.v0.draft.json 產生，是「如果雷達能輸出
這些，整條流程長這樣」，同時就是要向雷達負責人確認的輸出規格。

- 事件、換算規則、問題與回答都只在這個檔案；不改 src/ 的規則與範本，也不放進 data/。
- 換算規則 DEMO-DRAFT-0 未經 PT 審核，頁面與回答一直標明「草案」；正式規則要審核後才進 src/。
- 等級只由知識圖譜門檻決定；居服員看的句子來自 rewrite()，並通過 check_output()。

重新產生（tests/test_kg_demo.py 會檢查）：
    python demo/kg_build.py                  # → demo/kg_demo.html
    python demo/kg_build.py --fragment 路徑  # 不含 doctype 的版本，給 Artifact 發布用
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from radar_agent.caregiver import FORBIDDEN, check_output
from radar_agent.event_validation import validate_event
from radar_agent.kg_rules import Assessment, assess, load
from radar_agent.rewrite import rewrite

HERE = Path(__file__).resolve().parent
PLACEHOLDER = "/*KG_DATA*/null"
TZ = timezone(timedelta(hours=8))
DEVICE = "DEMO-SIM-KG-01"
SUBJECT = "DEMO-ELDER-01"
FIRST_DAY = datetime(2026, 10, 8, tzinfo=TZ)
DAYS = 7
SEED = 20261017

# 換算規則草案：雷達行走事件 → 一週的步行速度（m/s）。數字都是暫定，待 PT 審核（週一清單 K-1）。
@dataclass(frozen=True)
class Rule:
    id: str = "DEMO-DRAFT-0"
    status: str = "草案，未經 PT 審核"
    min_confidence: Decimal = Decimal("0.8")
    min_seconds: Decimal = Decimal("5")
    min_meters: Decimal = Decimal("3")
    min_walks: int = 10


RULE = Rule()
RULE_TEXT = [
    "只用 activity／walking 事件，信心值 0.8 以上、持續 5 秒以上、距離 3 公尺以上（排除室內短距離移動）",
    "每次步速 = 距離 ÷ 持續秒數",
    "一週取中位數，四捨五入到小數 2 位",
    "合格的行走少於 10 次：本週無法評估",
]
# 雷達量不到或沒有換算規則的指標：不出等級（docs/02 的三種狀態）
NOT_ASSESSED = [
    {"metric": "下肢肌力", "state": "no_rule",
     "why": "有坐→站事件，但標準施測是「30 秒內反覆起站」，日常生活的坐→站不能直接換算；要不要做替代量待決定（K-1）"},
    {"metric": "單腳站立", "state": "unmeasurable", "why": "雷達量不到"},
    {"metric": "來回行走", "state": "unmeasurable", "why": "2.44 公尺來回是施測動作，日常生活不會出現"},
    {"metric": "心肺耐力", "state": "unmeasurable", "why": "2 分鐘原地抬腳是施測動作，日常生活不會出現"},
]
# 邊界情境：假設這週的中位數剛好是 0.80（知識圖譜 v1 的門檻重疊，會議清單 E5）
BOUNDARY_VALUE = "0.80"


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


def simulated_events() -> list[dict[str, Any]]:
    """一週的模擬事件：每天 6～8 次行走（其中幾次是短距離移動）與 6～9 次坐→站。照 schema 草案，固定亂數種子。"""
    rng = random.Random(SEED)
    events: list[dict[str, Any]] = []

    def add(event_type: str, label: str, start: datetime, seconds: float, measurements: list[dict[str, Any]],
            confidence: float) -> None:
        end = start + timedelta(seconds=seconds)
        events.append({
            "schema_version": "0-draft",
            "event_id": f"SIM-KG-{len(events) + 1:04d}",
            "device_id": DEVICE,
            "subject_id": SUBJECT,
            "event_type": event_type,
            "label": label,
            "confidence": confidence,
            "measurements": measurements,
            "timestamps": {"detected": _iso(end), "started": _iso(start), "ended": _iso(end)},
        })

    for day in range(DAYS):
        base = FIRST_DAY + timedelta(days=day)
        moments = sorted(rng.sample(range(7 * 60, 21 * 60), 16))
        for i, minute in enumerate(moments):
            start = base + timedelta(minutes=minute, seconds=rng.randrange(60))
            if i % 2 == 0:
                short = rng.random() < 0.25
                meters = round(rng.uniform(1.2, 2.6) if short else rng.uniform(4.0, 9.0), 1)
                speed = rng.uniform(0.76, 0.94)
                add("activity", "walking", start, round(meters / speed, 1),
                    [{"name": "distance", "value": meters, "unit": "m"}], round(rng.uniform(0.76, 0.98), 2))
            else:
                add("posture", "sit_to_stand", start, round(rng.uniform(1.6, 3.4), 1), [], round(rng.uniform(0.8, 0.98), 2))
    return sorted(events, key=lambda e: e["timestamps"]["started"])


def _seconds(event: dict[str, Any]) -> Decimal:
    stamps = event["timestamps"]
    span = datetime.fromisoformat(stamps["ended"]) - datetime.fromisoformat(stamps["started"])
    return Decimal(str(span.total_seconds()))


def walking_speed(events: list[dict[str, Any]]) -> dict[str, Any]:
    """換算規則 DEMO-DRAFT-0：行走事件 → 一週步速中位數。回傳數值與用到、排除的事件編號。"""
    walks = [e for e in events if e["event_type"] == "activity" and e["label"] == "walking"]
    used: list[tuple[str, Decimal]] = []
    short: list[str] = []
    unsure: list[str] = []
    for e in walks:
        meters = Decimal(str(next(m["value"] for m in e["measurements"] if m["name"] == "distance")))
        seconds = _seconds(e)
        if seconds < RULE.min_seconds or meters < RULE.min_meters:
            short.append(e["event_id"])
        elif Decimal(str(e["confidence"])) < RULE.min_confidence:
            unsure.append(e["event_id"])
        else:
            used.append((e["event_id"], meters / seconds))
    value = None
    if len(used) >= RULE.min_walks:
        value = statistics.median(s for _, s in used).quantize(Decimal("0.01"), ROUND_HALF_UP)
    return {
        "value": None if value is None else str(value),
        "walks": len(walks), "used": [i for i, _ in used], "short": short, "unsure": unsure,
    }


def _wording(text: str) -> list[str]:
    return [f"不能出現「{w}」" for w in FORBIDDEN if w in text]


def _personal(a: Assessment) -> str:
    """居服員看的等級與建議：走正式的 rewrite()（沒有模型就是範本），並通過 check_output()。"""
    text: str = rewrite(a, profile="居服員").text
    problems = check_output(text, a)
    if problems:
        raise ValueError(f"輸出檢查沒通過：{problems}")
    return text


def kg_data() -> dict[str, Any]:
    events = simulated_events()
    for e in events:
        errors = validate_event(e)
        if errors:
            raise ValueError(f"{e['event_id']} 不符合事件格式草案：{errors}")
    kg = load()
    speed = walking_speed(events)
    if speed["value"] is None:
        raise ValueError("模擬資料的合格行走次數不足，請調整 simulated_events()")
    result = assess(kg, "步行速度", speed["value"])
    if result["status"] != "assessed":
        raise ValueError(f"模擬的步速 {speed['value']} 沒有落在單一等級：{result['reason']}")
    boundary = assess(kg, "步行速度", BOUNDARY_VALUE)
    if boundary["status"] == "assessed":
        raise ValueError(f"知識圖譜 {kg.info['version']} 的 {BOUNDARY_VALUE} 已不在門檻重疊，請改邊界情境")

    last = FIRST_DAY + timedelta(days=DAYS - 1)
    period = f"{FIRST_DAY.month}/{FIRST_DAY.day}–{last.month}/{last.day}"
    sit = sum(e["label"] == "sit_to_stand" for e in events)
    kg_note = f"知識圖譜 {kg.info['version']}（{'已核准' if kg.info['approved'] else '尚未核准，僅供測試'}）"
    rule_note = f"換算規則 {RULE.id}（{RULE.status}）"
    walk_ids = f"{speed['used'][0]}…{speed['used'][-1]} 中的 {len(speed['used'])} 筆"
    unmeasurable = "、".join(n["metric"] for n in NOT_ASSESSED if n["state"] == "unmeasurable")
    dialogue: list[dict[str, Any]] = [
        {"q": "這週走路的狀況怎麼樣？", "layer": "rewrite() 範本", "a": _personal(result),
         "basis": [rule_note, kg_note, f"行走事件 {walk_ids}"]},
        {"q": f"{speed['value']} 是怎麼算出來的？", "layer": "依據說明（固定句型）",
         "a": (f"{period} 共有 {speed['walks']} 次行走，其中 {len(speed['used'])} 次符合換算規則"
               f"（排除 {len(speed['short'])} 次短距離移動、{len(speed['unsure'])} 次信心值不足），"
               f"取每次步速的中位數為 {speed['value']} m/s。換算規則是草案，還沒有經過 PT 審核。"),
         "basis": [rule_note, f"使用 {walk_ids}；排除 {len(speed['short']) + len(speed['unsure'])} 筆"]},
        {"q": "起身和平衡的狀況呢？", "layer": "固定句型",
         "a": (f"這週有 {sit} 次從坐到站，但還沒有從日常起身換算下肢肌力的規則，本週無法評估。"
               f"{unmeasurable}雷達量不到，本週無法評估。"),
         "basis": [kg_note, f"坐→站事件 {sit} 筆（只計數，不出等級）"]},
        {"q": "要不要安排就醫？", "layer": "固定句型（超出範圍）",
         "a": "這個問題系統不回答：等級只依知識圖譜的門檻，不做醫療判斷。如果擔心，請和護理人員或主管討論。",
         "basis": ["超出系統範圍的問題一律不回答（非醫療器材）"]},
    ]
    for turn in dialogue[1:]:
        problems = _wording(turn["a"])
        if problems:
            raise ValueError(f"「{turn['q']}」的回答：{problems}")

    return {
        "device": DEVICE, "subject": SUBJECT, "period": period,
        "kg": kg.info, "rule": {"id": RULE.id, "status": RULE.status, "text": RULE_TEXT},
        "events": [
            {"id": e["event_id"], "type": e["event_type"], "label": e["label"], "confidence": e["confidence"],
             "start": e["timestamps"]["started"], "seconds": str(_seconds(e)),
             "meters": next((m["value"] for m in e["measurements"] if m["name"] == "distance"), None),
             "used": e["event_id"] in speed["used"]}
            for e in events
        ],
        "speed": speed,
        "assessment": {k: result[k] for k in ("metric", "value", "unit", "score", "rank", "source_text",
                                              "suggestion", "recommended_dose")},
        "caregiver": _personal(result),
        "boundary": {"value": BOUNDARY_VALUE, "reason": boundary["reason"], "caregiver": _personal(boundary)},
        "not_assessed": NOT_ASSESSED,
        "dialogue": dialogue,
        "sample_event": events[0],
    }


def fragment(data: dict[str, Any]) -> str:
    template = (HERE / "kg_template.html").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, indent=1).replace("</", "<\\/")
    return template.replace(PLACEHOLDER, payload)


def page(data: dict[str, Any]) -> str:
    head = '<!doctype html>\n<html lang="zh-Hant">\n<meta charset="utf-8">\n'
    head += '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
    return head + fragment(data) + "</html>\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fragment", type=Path, help="改寫出不含 doctype 的版本到這個路徑（Artifact 發布用）")
    args = ap.parse_args()
    data = kg_data()
    out = args.fragment or HERE / "kg_demo.html"
    out.write_text(fragment(data) if args.fragment else page(data), encoding="utf-8")
    print(f"寫入 {out}")
