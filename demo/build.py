"""產生 Demo 原型頁 demo/prototype.html（docs/11「Demo 原型頁」）。

頁面上照護人員看到的每一句話都來自 radar-caregiver（`caregiver_report`、`trend_text`）與 radar-ask（`pick`），
這裡只把它們放進畫面：事件依時間排成通知卡片，照護紀錄草稿逐行累積，最後是兩週使用趨勢。
資料是 data/samples_demo/ 的**模擬資料**，頁面頂端一直標明。

重新產生（改了 template.html、caregiver.py 的用語或模擬資料後都要跑；tests/test_prototype.py 會檢查）：
    python demo/build.py                  # → demo/prototype.html（可直接用瀏覽器開，不需網路也能顯示）
    python demo/build.py --fragment 路徑  # 不含 doctype 的版本，給 Artifact 發布用

實測資料版（docs/11「10/19 實測資料」）：先用 radar-field-data mask 遮罩，再
    python demo/build.py --events outputs/field/FIELD-01.jsonl --place "1 號廁所"
寫到 outputs/field_prototype.html（不會被 commit）。頁面橫幅改成「實測資料」，沒有兩週趨勢。
還有像 MAC 的字串、或輸出位置會被 commit 時，拒絕產生。
"""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import demo_page  # demo/ 底下的共用外殼；直接執行或測試時 demo/ 都在 sys.path
from radar_agent.caregiver import (
    OutputRejected,
    check_trend,
    report_data,
    trend_text,
    usage_trend,
)
from radar_agent.device_events import Summary, read_records, summarize
from radar_agent.event_ask import EVENT_QUESTIONS, pick
from radar_agent.field_data import MAC_LIKE, check_out_dir

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PLACE = "1 號廁所"
PLACEHOLDER = "/*DEMO_DATA*/null"

def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("demo_generate", REPO / "data" / "samples_demo" / "generate.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ask_data(summary: Summary, place: str) -> list[dict[str, str]]:
    """事件提問的六題與答案：和 radar-ask --pick 同一套函式（不經模型），答案先通過輸出檢查。"""
    out = []
    for q in EVENT_QUESTIONS:
        reply = pick(q.id, summary, place=place)
        if reply.answer is None:
            raise ValueError(f"事件提問的答案沒通過輸出檢查：{q.id} {reply.problems}")
        out.append({"id": q.id, "text": q.text, "answer": reply.answer})
    return out


def demo_data(events: Path | None = None, place: str = PLACE,
              records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """events 為 None 時用 Demo 模擬資料（含兩週趨勢）；否則是遮罩過的實測資料（不含趨勢）。

    records：直接給另一份模擬紀錄（例如看板的「平靜的上午」），一樣附兩週趨勢。只接受程式產生的模擬紀錄：
    這條路不做 MAC 檢查，實測資料一律走 events（先遮罩）。
    """
    if events is not None:
        if MAC_LIKE.search(events.read_text(encoding="utf-8")):
            raise ValueError(f"{events} 裡還有像 MAC 的字串，請先用 radar-field-data mask 遮罩")
        records = read_records(events)
    elif records is None:
        records = read_records(REPO / "data" / "samples_demo" / "bathroom_morning.jsonl")
    summary = summarize(records)
    if not summary.online:
        raise ValueError("沒有讀得懂的紀錄，無法產生頁面")
    # 卡片、事件摘要與斷線都直接取 radar-caregiver --json 的結構化輸出（輸出檢查在裡面），不從文字切回來
    try:
        report = report_data(summary, place=place)
    except OutputRejected as e:
        raise ValueError(f"radar-caregiver 輸出檢查沒通過：{e.problems}") from e

    data: dict[str, Any] = {
        "source": "field" if events is not None else "sim",
        "place": place,
        "day": f"{summary.online[0].start:%Y-%m-%d}",
        "from": report["from"],
        "to": report["to"],
        "highlights": report["highlights"],
        "summary": report["summary"],
        "visits": [[f"{v.start:%Y-%m-%d %H:%M:%S}", f"{v.end:%Y-%m-%d %H:%M:%S}"] for v in summary.visits],
        "offline": report["counts"]["offline"],
        "events": report["events"],
        "ask": ask_data(summary, place),
        "trend": None,
    }
    if events is not None:
        return data

    trend = usage_trend(_load_generator().generate_two_weeks())
    trend_lines = trend_text(trend, place=place)
    problems = check_trend(trend_lines, trend, place=place)
    if problems or trend is None:
        raise ValueError(f"使用趨勢輸出檢查沒通過：{problems}")
    data["trend"] = {
        "lines": trend_lines.splitlines(),
        "days": [
            {"day": f"{c.day.month}/{c.day.day}", "visits": c.visits, "dawn": c.dawn, "recent": i >= 7}
            for i, c in enumerate(trend.before + trend.recent)
        ],
    }
    return data


def fragment(data: dict[str, Any]) -> str:
    return demo_page.fragment(HERE / "template.html", PLACEHOLDER, data)


def page(data: dict[str, Any]) -> str:
    return demo_page.page(HERE / "template.html", PLACEHOLDER, data)


FIELD_OUT = REPO / "outputs" / "field_prototype.html"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fragment", type=Path, help="改寫出不含 doctype 的版本到這個路徑（Artifact 發布用）")
    ap.add_argument("--events", type=Path, help="改用遮罩過的實測資料（radar-field-data mask 的輸出）")
    ap.add_argument("--place", default=PLACE, help=f"地點名稱，預設「{PLACE}」")
    args = ap.parse_args()
    if args.events:
        if args.fragment:
            raise SystemExit("錯誤：實測資料版不提供 --fragment；實測資料不發布到線上")
        problem = check_out_dir(FIELD_OUT.parent)
        if problem:
            raise SystemExit(f"錯誤：{problem}")
        FIELD_OUT.parent.mkdir(exist_ok=True)
        FIELD_OUT.write_text(page(demo_data(args.events, args.place)), encoding="utf-8")
        print(f"寫入 {FIELD_OUT}（實測資料，不要 commit、不要發布）")
        raise SystemExit(0)
    data = demo_data()
    if args.fragment:
        args.fragment.write_text(fragment(data), encoding="utf-8")
        print(f"寫入 {args.fragment}")
    else:
        (HERE / "prototype.html").write_text(page(data), encoding="utf-8")
        print(f"寫入 {HERE / 'prototype.html'}")
