"""產生知識圖譜 Demo 頁 demo/kg_demo.html（**模擬資料**，內部確認 Demo 功能用；docs/11「知識圖譜 Demo」）。

一位長者一週的模擬姿態與活動事件 → 換算規則（**草案**）→ 量測值 → 知識圖譜門檻（radar-kg-level 的
`assess`）→ 居服員看的等級與建議（`rewrite()`，目前是範本）→ 依讀者（Profile）切換的固定問題與回答，每個回答都附依據
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
import random
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import demo_page  # demo/ 底下的共用外殼；直接執行或測試時 demo/ 都在 sys.path
from radar_agent.output_check import FORBIDDEN, check_output
from radar_agent.event_validation import validate_event
from radar_agent.audience import ALLOWED, check_audience
from radar_agent.kg_rules import Assessment, KgInfo, assess, load
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


# 各 Profile 的固定問題（docs/02「輸出的對象」、audience.py）。居服員的等級句子來自 rewrite()；
# 其他 Profile 還沒有正式範本，句子是 Demo 草案（標 draft），題目與範本要經審核（單向門「給讀者的輸出與用語」）。
OUT_OF_SCOPE = "這個問題系統不回答：等級只依知識圖譜的門檻，不做醫療判斷。如果擔心，請和護理人員討論。"
BASIS_OPEN = {"居服員": True, "醫師": True, "家屬": False, "長者本人": False, "主管機關": False}


def _q(qid: str, q: str, a: str, layer: str, basis: list[str], follow: tuple[str, ...] = (),
       *, draft: bool = False, checked: bool = False) -> dict[str, Any]:
    return {"id": qid, "q": q, "a": a, "layer": layer, "basis": basis, "follow": list(follow),
            "draft": draft, "checked": checked}


def profile_menus(kg_info: KgInfo, speed: dict[str, Any], a: Assessment, boundary: Assessment,
                  period: str, sit: int) -> list[dict[str, Any]]:
    """每個 Profile 的題目選單。權限照 audience.ALLOWED：不能看個人資料的 Profile 沒有題目，只寫原因。"""
    kg_note = f"知識圖譜 {kg_info['version']}（{'已核准' if kg_info['approved'] else '尚未核准，僅供測試'}）"
    test_only = "" if kg_info["approved"] else "（知識圖譜尚未核准，僅供測試）"
    rule_note = f"換算規則 {RULE.id}（{RULE.status}）"
    used, dropped = len(speed["used"]), len(speed["short"]) + len(speed["unsure"])
    walk_ids = f"行走事件 {speed['used'][0]}…{speed['used'][-1]} 中的 {used} 筆"
    value = f"{speed['value']} {a['unit']}"
    unmeasurable = "、".join(n["metric"] for n in NOT_ASSESSED if n["state"] == "unmeasurable")
    how = (f"{period} 共有 {speed['walks']} 次行走，其中 {used} 次符合換算規則"
           f"（排除 {len(speed['short'])} 次短距離移動、{len(speed['unsure'])} 次信心值不足），"
           f"取每次步速的中位數為 {value}。換算規則是草案，還沒有經過 PT 審核。")
    others = (f"這週有 {sit} 次從坐到站，但還沒有從日常起身換算下肢肌力的規則，本週無法評估。"
              f"{unmeasurable}雷達量不到，本週無法評估。")
    advice = f"建議：{a['suggestion']}\n建議量：{a['recommended_dose']}\n{test_only}".rstrip()
    no_compare = "目前只有一週的資料，還不能和上週比較，本週無法評估變化。"
    out = _q("out", "其他問題", OUT_OF_SCOPE, "固定句型（超出範圍）", ["超出系統範圍的問題一律不回答（非醫療器材）"])

    menus: dict[str, list[dict[str, Any]]] = {
        "居服員": [
            _q("walk", "這週走路的狀況怎麼樣？", _personal(a), "rewrite() 範本",
               [rule_note, kg_note, walk_ids], ("how", "others"), checked=True),
            _q("how", f"{speed['value']} 是怎麼算出來的？", how, "依據說明（固定句型）",
               [rule_note, f"使用 {walk_ids}；排除 {dropped} 筆"], ("others",)),
            _q("others", "起身和平衡的狀況呢？", others, "固定句型",
               [kg_note, f"坐→站事件 {sit} 筆（只計數，不出等級）"], ("walk",)),
            out,
        ],
        "家屬": [
            _q("walk", "這週活動的狀況如何？",
               f"這週走路的速度屬於「{a['rank']}」。{test_only}", "Demo 草案範本",
               [kg_note], ("advice", "compare"), draft=True, checked=True),
            _q("advice", "可以怎麼陪伴活動？", advice, "知識圖譜原文照抄", [kg_note], ("walk",),
               draft=True, checked=True),
            _q("compare", "和上週比起來呢？", no_compare, "固定句型", ["只有一週資料"], ("walk",), draft=True),
            out,
        ],
        "醫師": [
            _q("value", "量測值與門檻原文", (
                f"{a['metric']} {value}（{used} 次行走的中位數），落在知識圖譜門檻「{a['source_text']}」，"
                f"{a['score']} 分「{a['rank']}」。{test_only}"),
               "Demo 草案範本", [rule_note, kg_note, walk_ids], ("data", "rule"), draft=True),
            _q("data", "資料夠不夠？", how, "依據說明（固定句型）",
               [f"使用 {walk_ids}；排除 {dropped} 筆", f"規則要求至少 {RULE.min_walks} 次"], ("rule",), draft=True),
            _q("rule", "依據哪一版規則？", f"{rule_note}：" + "；".join(RULE_TEXT) + "。",
               "固定句型", [rule_note, kg_note], ("unassessed",), draft=True),
            _q("unassessed", "哪些項目無法評估、為什麼？",
               f"{others}另外，步速剛好 {boundary['value']} {a['unit']} 時無法評估：{boundary['reason']}。",
               "固定句型（含審核用的原因）", [kg_note], ("value",), draft=True),
            out,
        ],
        "長者本人": [
            _q("walk", "我這週走得怎麼樣？",
               f"您這週走路的速度屬於「{a['rank']}」。{test_only}", "Demo 草案範本", [kg_note], ("advice",),
               draft=True, checked=True),
            _q("advice", "我可以做什麼運動？", advice, "知識圖譜原文照抄", [kg_note], ("walk",),
               draft=True, checked=True),
            out,
        ],
    }
    result = []
    for profile in ALLOWED:
        refused = check_audience(profile, a)
        questions = [] if refused else menus[profile]
        for q in questions:
            problems = _wording(q["a"]) if not q["checked"] else check_output(q["a"], a)
            if problems:
                raise ValueError(f"{profile}「{q['q']}」的回答：{problems}")
            ids = {x["id"] for x in questions}
            if not set(q["follow"]) <= ids:
                raise ValueError(f"{profile}「{q['q']}」的延伸問題不在選單裡")
        result.append({"profile": profile, "refused": refused, "basis_open": BASIS_OPEN[profile],
                       "questions": questions})
    return result


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
    menus = profile_menus(kg.info, speed, result, boundary, period, sit)

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
        "profiles": menus,
        "sample_event": events[0],
    }


def fragment(data: dict[str, Any]) -> str:
    return demo_page.fragment(HERE / "kg_template.html", PLACEHOLDER, data)


def page(data: dict[str, Any]) -> str:
    return demo_page.page(HERE / "kg_template.html", PLACEHOLDER, data)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fragment", type=Path, help="改寫出不含 doctype 的版本到這個路徑（Artifact 發布用）")
    args = ap.parse_args()
    data = kg_data()
    out = args.fragment or HERE / "kg_demo.html"
    out.write_text(fragment(data) if args.fragment else page(data), encoding="utf-8")
    print(f"寫入 {out}")
