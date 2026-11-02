"""Demo 頁面共用的資料：樣本或實測資料 → 頁面資料（2026-11-02 從 build.py 抽出來）。

原型頁（build.py）、看板（dashboard_build.py）、Jetson 內部 Demo（jetson_serve.py）與流程確認頁（flow_build.py）
都從這裡拿樣本路徑、實測資料的檢查與頁面資料，不再照檔案路徑載入 build.py（名稱 build 會和 PyPI 的 build 套件撞名）。
頁面上照護人員看到的每一句話都來自 radar-caregiver（report_data、trend_text）與 radar-ask（pick），這裡只組資料。

實測資料一律先用 radar-field-data mask 遮罩：還有像 MAC 的字串就拒絕（check_masked）；
寫出實測資料版頁面的位置不能是會被 commit 的地方（field_out）。改這兩項屬單向門「個人資料的流向」。
"""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from radar_agent.caregiver import OutputRejected, check_trend, report_data, trend_text, usage_trend
from radar_agent.device_events import Summary, read_records, summarize
from radar_agent.event_ask import EVENT_QUESTIONS, pick
from radar_agent.field_data import MAC_LIKE, check_out_dir

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SAMPLE = REPO / "data" / "samples_demo" / "bathroom_morning.jsonl"  # 模擬的一個上午（DEMO-SIM-01）
PLACE = "1 號廁所"
# 原型頁的範本：build.py 產生 prototype.html，jetson_serve.py 在 Jetson 上即時產生同一頁
TEMPLATE = HERE / "template.html"
PLACEHOLDER = "/*DEMO_DATA*/null"


def check_masked(events: Path) -> None:
    """實測資料還有像 MAC 的字串就丟 ValueError：要先用 radar-field-data mask 遮罩。"""
    if MAC_LIKE.search(events.read_text(encoding="utf-8")):
        raise ValueError(f"{events} 裡還有像 MAC 的字串，請先用 radar-field-data mask 遮罩")


def field_out(path: Path) -> Path:
    """實測資料版頁面要寫到的位置：會被 commit 的地方丟 ValueError；可以寫就建好資料夾、回傳原路徑。"""
    problem = check_out_dir(path.parent)
    if problem:
        raise ValueError(problem)
    path.parent.mkdir(exist_ok=True)
    return path


@dataclass(frozen=True)
class Source:
    """一份要放進頁面的資料：模擬（sim）或遮罩過的實測（field）。summary 只算一次，各頁共用。"""

    records: list[dict[str, Any]]
    summary: Summary
    place: str
    field: bool


def load(events: Path | None = None, place: str = PLACE, records: list[dict[str, Any]] | None = None) -> Source:
    """events 為遮罩過的實測資料路徑（先檢查 MAC）；都沒給就是 Demo 模擬資料。

    records：直接給另一份模擬紀錄（例如看板的「平靜的上午」）。只接受程式產生的模擬紀錄：
    這條路不做 MAC 檢查，實測資料一律走 events。
    """
    if events is not None:
        check_masked(events)
        records = read_records(events)
    elif records is None:
        records = read_records(SAMPLE)
    return Source(records, summarize(records), place, events is not None)


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


def page_data(source: Source) -> dict[str, Any]:
    """頁面資料。模擬資料附兩週趨勢；實測資料不附（只有一段時間）。"""
    summary, place = source.summary, source.place
    if not summary.online:
        raise ValueError("沒有讀得懂的紀錄，無法產生頁面")
    # 卡片、事件摘要與斷線都直接取 radar-caregiver --json 的結構化輸出（輸出檢查在裡面），不從文字切回來
    try:
        report = report_data(summary, place=place)
    except OutputRejected as e:
        raise ValueError(f"radar-caregiver 輸出檢查沒通過：{e.problems}") from e

    data: dict[str, Any] = {
        "source": "field" if source.field else "sim",
        "place": place,
        "day": report["from"][:10],  # 時段一律取報告的 from／to（第一筆到最後一筆在線）
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
    if source.field:
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


def demo_data(events: Path | None = None, place: str = PLACE,
              records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """load() 再 page_data()：只要頁面資料、不需要 Summary 的呼叫端用。"""
    return page_data(load(events, place, records))
