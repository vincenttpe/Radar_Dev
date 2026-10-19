"""產生 Demo 原型頁 demo/prototype.html（docs/11「Demo 原型頁」）。

頁面上照護人員看到的每一句話都來自 radar-caregiver（`caregiver_report`、`trend_text`），
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
import json
from pathlib import Path
from types import ModuleType
from typing import Any

from radar_agent.caregiver import (
    NOTE_KINDS,
    caregiver_report,
    check_trend,
    event_notes,
    trend_text,
    usage_trend,
)
from radar_agent.device_events import summarize
from radar_agent.field_data import MAC_LIKE, check_out_dir
from radar_agent.output_check import check_output

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PLACE = "1 號廁所"
PLACEHOLDER = "/*DEMO_DATA*/null"

# 處置提示 → （卡片等級, 卡片標題）：和 radar-caregiver --json 用同一份對照（caregiver.NOTE_KINDS）。
KINDS = NOTE_KINDS


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("demo_generate", REPO / "data" / "samples_demo" / "generate.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _section(report: str, heading: str) -> list[str]:
    lines = report.splitlines()
    start = lines.index(heading) + 1
    end = next((i for i in range(start, len(lines)) if not lines[i]), len(lines))
    return lines[start:end]


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def demo_data(events: Path | None = None, place: str = PLACE) -> dict[str, Any]:
    """events 為 None 時用 Demo 模擬資料（含兩週趨勢）；否則是遮罩過的實測資料（不含趨勢）。"""
    if events is not None:
        if MAC_LIKE.search(events.read_text(encoding="utf-8")):
            raise ValueError(f"{events} 裡還有像 MAC 的字串，請先用 radar-field-data mask 遮罩")
        records = _jsonl(events)
    else:
        records = _jsonl(REPO / "data" / "samples_demo" / "bathroom_morning.jsonl")
    summary = summarize(records)
    if not summary.online:
        raise ValueError("沒有讀得懂的紀錄，無法產生頁面")
    report = caregiver_report(summary, place=place)
    problems = check_output(report, summary, place=place)
    if problems:
        raise ValueError(f"radar-caregiver 輸出檢查沒通過：{problems}")

    notes = event_notes(summary, place)
    prompts = _section(report, "【處置提示】") if notes else []
    drafts = _section(report, "【照護紀錄草稿】") if notes else []
    cards = []
    for note, prompt_line, draft in zip(notes, prompts, drafts, strict=True):
        label = note.span or f"{note.time:%H:%M}"
        if prompt_line != f"{label} {note.prompt}" or draft != note.record:
            raise ValueError(f"處置提示與事件對不上：{prompt_line}")
        if note.prompt not in KINDS:
            raise ValueError(f"build.py 的 KINDS 沒有這句處置提示，請補上：{note.prompt}")
        level, title = KINDS[note.prompt]
        cards.append({
            "at": f"{note.time:%Y-%m-%d %H:%M:%S}", "label": label, "level": level, "title": title,
            "prompt": note.prompt, "record": note.record,
        })

    data: dict[str, Any] = {
        "source": "field" if events is not None else "sim",
        "place": place,
        "day": f"{summary.online[0].start:%Y-%m-%d}",
        "from": f"{summary.online[0].start:%Y-%m-%d %H:%M:%S}",
        "to": f"{summary.online[-1].end:%Y-%m-%d %H:%M:%S}",
        "summary": _section(report, "【事件摘要】")[0],
        "visits": [[f"{v.start:%Y-%m-%d %H:%M:%S}", f"{v.end:%Y-%m-%d %H:%M:%S}"] for v in summary.visits],
        "offline": [
            [f"{a.end:%Y-%m-%d %H:%M:%S}", f"{b.start:%Y-%m-%d %H:%M:%S}"] for a, b in zip(summary.online, summary.online[1:], strict=False)
        ],
        "events": cards,
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
    template = (HERE / "template.html").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, indent=1).replace("</", "<\\/")
    return template.replace(PLACEHOLDER, payload)


def page(data: dict[str, Any]) -> str:
    head = '<!doctype html>\n<html lang="zh-Hant">\n<meta charset="utf-8">\n'
    head += '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
    return head + fragment(data) + "</html>\n"


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
