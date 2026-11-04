"""產生下肢三項測驗 Demo 頁 demo/fitness_demo.html（**模擬資料、草案**，內部確認可行性用）。

ADR 0012（提議）的流程走一遍：指定測驗時段的雷達事件 → 量測（30 秒坐站次數、4 公尺步行速度、
2.44 公尺來回秒數）→ 體育署 i 運動 115 年官方表（一般檢測樂活組，分年齡與性別）判定五等級（docs/05 E5 選項 e），
旁邊列出知識圖譜 v1（radar-kg-level 的 assess）的結果對照。

- 只在 Demo：不改 kg/、不改 src/ 的等級判定；官方表放在 demo/fitness_norms_115.json，不是知識圖譜、未經審核。
- 長者、裝置、事件全部是模擬的；年齡與性別也是模擬值（正式使用屬單向門「個人資料的流向」）。
- 量測規則（FT-DRAFT-0）是草案：測驗時段由誰開始、雷達能不能輸出這些事件，都待確認（docs/05 E7、A45–A50）。
- 官方表「每格是下限」是推定；2.44 公尺（動態平衡與敏捷力）方向待確認，不分級；步行速度官方表沒有，不分級。
- 確認可行之後，再向運動部申請 106～114 年資料與寶典原文（使用者 2026-11-04 決定）。

重新產生（tests/test_fitness_demo.py 會檢查）：
    python demo/fitness_build.py
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import demo_page  # demo/ 底下的共用外殼；直接執行或測試時 demo/ 都在 sys.path
from radar_agent.kg_rules import assess, load

HERE = Path(__file__).resolve().parent
PLACEHOLDER = "/*FT_DATA*/null"
NORMS = json.loads((HERE / "fitness_norms_115.json").read_text(encoding="utf-8"))
TZ = timezone(timedelta(hours=8))
DEVICE = "DEMO-SIM-FT-01"
SUBJECT = {"id": "DEMO-ELDER-02", "sex": "female", "age": 72}  # 模擬長者
SEX_TEXT = {"female": "女", "male": "男"}
MIN_CONFIDENCE = Decimal("0.8")
DAY = datetime(2026, 11, 2, 10, 0, tzinfo=TZ)

# 測驗時段：假設由現場人員在畫面上按「開始」，系統記下開始時間（誰按、怎麼按待確認，docs/05 E7）
SESSIONS = [
    {"key": "chair_stand", "test": "30 秒椅子坐立", "started": DAY, "window_s": 30},
    {"key": "gait_speed", "test": "4 公尺步行", "started": DAY + timedelta(minutes=5), "window_s": 60, "course_m": "4"},
    {"key": "up_and_go", "test": "2.44 公尺椅子坐起繞物", "started": DAY + timedelta(minutes=10), "window_s": 60},
    # 同一天重測一次坐站，但雷達有幾次起身的信心值不足：示範「量測限制 → 無法評估」
    {"key": "chair_stand_retry", "test": "30 秒椅子坐立（重測）", "started": DAY + timedelta(minutes=20), "window_s": 30},
]
RULES = {  # 量測規則草案 FT-DRAFT-0（未經 PT 審核）
    "chair_stand": ["測驗時段開始後 30 秒內「站好」（坐→站事件結束）的次數", "時段內任何一次坐→站信心值低於 0.8：次數不可靠，無法評估"],
    "gait_speed": ["測驗時段內恰好一筆行走事件，信心值 0.8 以上", "步速 = 步道長度 4 公尺 ÷ 行走秒數，四捨五入到小數 2 位"],
    "up_and_go": ["從測驗時段開始（發令）到站→坐事件結束的秒數，四捨五入到小數 1 位", "時段內的事件信心值都要 0.8 以上"],
}
RULES["chair_stand_retry"] = RULES["chair_stand"]
# 每項測驗對應的官方表項目與知識圖譜 v1 指標
MAPPING = {
    "chair_stand": ("下肢肌力", "下肢肌力"), "chair_stand_retry": ("下肢肌力", "下肢肌力"),
    "gait_speed": (None, "步行速度"), "up_and_go": ("動態平衡與敏捷力", "來回行走"),
}


def _s(seconds: float) -> timedelta:
    """秒數 → 整數毫秒的 timedelta：時間戳只到毫秒，避免浮點誤差讓秒數差 0.001。"""
    return timedelta(milliseconds=round(seconds * 1000))


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="milliseconds")


def simulated_events() -> list[dict[str, Any]]:
    """每個測驗時段的模擬事件，照 schemas/event.v0.draft.json。固定數值，不用亂數。"""
    events: list[dict[str, Any]] = []

    def add(event_type: str, label: str, start: datetime, seconds: float, confidence: float,
            measurements: list[dict[str, Any]] | None = None) -> None:
        end = start + _s(seconds)
        events.append({
            "schema_version": "0-draft", "event_id": f"SIM-FT-{len(events) + 1:04d}", "device_id": DEVICE,
            "subject_id": SUBJECT["id"], "event_type": event_type, "label": label, "confidence": confidence,
            "measurements": measurements or [],
            "timestamps": {"detected": _iso(end), "started": _iso(start), "ended": _iso(end)},
        })

    chair, gait, tug, retry = (s["started"] for s in SESSIONS)
    for i in range(16):  # 每 1.95 秒起身一次；第 16 次在 30 秒之後才站好
        add("posture", "sit_to_stand", chair + _s(0.4 + i * 1.95), 1.1, 0.9)
    add("activity", "walking", gait + _s(1.2), 4.6, 0.92, [{"name": "distance", "value": 4.1, "unit": "m"}])
    add("posture", "sit_to_stand", tug + _s(0.6), 1.2, 0.9)
    add("activity", "walking", tug + _s(1.8), 4.1, 0.88, [{"name": "distance", "value": 5.0, "unit": "m"}])
    add("posture", "stand_to_sit", tug + _s(6.1), 1.3, 0.9)
    for i in range(14):
        add("posture", "sit_to_stand", retry + _s(0.5 + i * 2.1), 1.2, 0.62 if i in (3, 9) else 0.9)
    return sorted(events, key=lambda e: e["timestamps"]["started"])


def _span(event: dict[str, Any]) -> tuple[datetime, datetime]:
    t = event["timestamps"]
    return datetime.fromisoformat(t["started"]), datetime.fromisoformat(t["ended"])


def _in_window(events: list[dict[str, Any]], session: dict[str, Any]) -> list[dict[str, Any]]:
    start = session["started"]
    end = start + timedelta(seconds=session["window_s"])
    return [e for e in events if start <= _span(e)[0] < end]


def _unsure(events: list[dict[str, Any]]) -> list[str]:
    return [e["event_id"] for e in events if Decimal(str(e["confidence"])) < MIN_CONFIDENCE]


def measure(session: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    """一個測驗時段 → 量測值（字串，無法評估時 None）、用到的事件與原因。"""
    key = session["key"]
    window = _in_window(events, session)
    unsure = _unsure(window)
    result: dict[str, Any] = {"value": None, "used": [], "late": [], "unsure": unsure, "reason": None}
    if unsure:
        result["reason"] = f"測驗時段內有 {len(unsure)} 筆事件信心值低於 {MIN_CONFIDENCE}，量測不可靠，無法評估"
        return result
    start = session["started"]
    if key in ("chair_stand", "chair_stand_retry"):
        stands = [e for e in window if e["label"] == "sit_to_stand"]
        limit = start + timedelta(seconds=30)
        result["used"] = [e["event_id"] for e in stands if _span(e)[1] <= limit]
        result["late"] = [e["event_id"] for e in stands if _span(e)[1] > limit]
        result["value"] = str(len(result["used"]))
    elif key == "gait_speed":
        walks = [e for e in window if e["label"] == "walking"]
        if len(walks) != 1:
            result["reason"] = f"測驗時段內有 {len(walks)} 筆行走事件（要恰好 1 筆），無法評估"
            return result
        begin, end = _span(walks[0])
        seconds = Decimal(str((end - begin).total_seconds()))
        result["used"] = [walks[0]["event_id"]]
        result["value"] = str((Decimal(session["course_m"]) / seconds).quantize(Decimal("0.01"), ROUND_HALF_UP))
    elif key == "up_and_go":
        sits = [e for e in window if e["label"] == "stand_to_sit"]
        if not sits:
            result["reason"] = "測驗時段內沒有站→坐事件，無法評估"
            return result
        end = _span(sits[-1])[1]
        result["used"] = [e["event_id"] for e in window]
        seconds = Decimal(str((end - start).total_seconds()))
        result["value"] = str(seconds.quantize(Decimal("0.1"), ROUND_HALF_UP))
    else:  # 新的測驗要先寫量測規則，不能默默套用其他測驗的算法
        raise ValueError(f"不認得的測驗：{key}")
    return result


def official_grade(item: str | None, sex: str, age: int, value: object) -> dict[str, Any]:
    """115 年官方表（一般檢測樂活組）的五等級。下限的讀法是推定（含下限）；不能用的情況回 not_graded 與原因。"""
    if item is None:
        return {"status": "not_graded", "reason": "官方表沒有步行速度這一項；切點另依文獻（docs/25 Q1 來源 4），待審核人決定"}
    spec = NORMS["items"][item]
    if not spec["direction_confirmed"]:
        return {"status": "not_graded", "reason": spec["direction_note"], "item": item}
    groups = [g for g in spec[sex] if g["min_age"] <= age and (g["max_age"] is None or age <= g["max_age"])]
    if not groups:
        return {"status": "not_graded", "reason": f"官方樂活組只有 65 歲以上，{age} 歲沒有對應的年齡組", "item": item}
    group = groups[0]
    index = sum(1 for bound in group["lower_bounds"] if Decimal(str(value)) >= Decimal(str(bound)))
    return {"status": "graded", "item": item, "level": NORMS["levels"][index], "pr": NORMS["pr"][index],
            "ages": group["ages"], "lower_bounds": group["lower_bounds"], "reason": None}


def fitness_data() -> dict[str, Any]:
    events = simulated_events()
    kg = load()
    tests: dict[str, Any] = {}
    for session in SESSIONS:
        key = session["key"]
        item, metric = MAPPING[key]
        measured = measure(session, events)
        value = measured["value"]
        if value is None:
            official = {"status": "not_graded", "reason": "沒有量測值"}
            v1 = None
        else:
            official = official_grade(item, SUBJECT["sex"], SUBJECT["age"], value)
            got = assess(kg, metric, value)
            v1 = {k: got[k] for k in ("status", "score", "rank", "source_text", "reason")}
        tests[key] = {"test": session["test"], "started": _iso(session["started"]), "rules": RULES[key],
                      "official_item": item, "v1_metric": metric, "measure": measured, "official": official, "v1": v1}
    return {
        "subject": {**SUBJECT, "sex_text": SEX_TEXT[SUBJECT["sex"]]}, "device": DEVICE, "rule_id": "FT-DRAFT-0",
        "norms": {"source": NORMS["source"], "reading": NORMS["reading"], "levels": NORMS["levels"], "pr": NORMS["pr"]},
        "kg": kg.info, "tests": tests,
        "events": [{"id": e["event_id"], "label": e["label"], "start": e["timestamps"]["started"][11:23],
                    "seconds": str(Decimal(str((_span(e)[1] - _span(e)[0]).total_seconds()))),
                    "confidence": e["confidence"]} for e in events],
    }


def page(data: dict[str, Any]) -> str:
    return demo_page.page(HERE / "fitness_template.html", PLACEHOLDER, data)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.parse_args()
    out = HERE / "fitness_demo.html"
    out.write_text(page(fitness_data()), encoding="utf-8")
    print(f"寫入 {out}")
