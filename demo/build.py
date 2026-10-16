"""產生 Demo 原型頁 demo/prototype.html（docs/11「Demo 原型頁」）。

頁面上照護人員看到的每一句話都來自 radar-caregiver（`caregiver_report`、`trend_text`），
這裡只把它們放進畫面：事件依時間排成通知卡片，照護紀錄草稿逐行累積，最後是兩週使用趨勢。
資料是 data/samples_demo/ 的**模擬資料**，頁面頂端一直標明。

重新產生（改了 template.html、caregiver.py 的用語或模擬資料後都要跑；tests/test_prototype.py 會檢查）：
    python demo/build.py                  # → demo/prototype.html（可直接用瀏覽器開，不需網路也能顯示）
    python demo/build.py --fragment 路徑  # 不含 doctype 的版本，給 Artifact 發布用
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

from radar_agent.caregiver import (
    caregiver_report,
    check_output,
    check_trend,
    event_notes,
    trend_text,
    usage_trend,
)
from radar_agent.device_events import summarize

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PLACE = "1 號廁所"
PLACEHOLDER = "/*DEMO_DATA*/null"

# 處置提示 → （卡片等級, 卡片標題）。標題用計畫書三項機制的名稱，等級決定顏色。
# caregiver.py 的處置提示一改，這裡認不得就會失敗，提醒同步更新畫面。
KINDS: dict[str, tuple[str, str]] = {
    "請立即到場確認。": ("urgent", "跌倒警報"),
    "長者回覆需要協助，請立即到場。": ("urgent", "跌倒警報"),
    "長者主動呼救，請立即到場。": ("urgent", "主動呼救"),
    "長者回覆沒事，警報已解除；可視情況前往關心。": ("resolved", "回覆沒事"),
    "已偵測到起身，警報已解除。": ("resolved", "起身解除"),
    "停留超過設定時間，請前往查看。": ("check", "久待警報"),
    "這段時間無法判斷是否有跌倒，請通知資訊人員。": ("device", "裝置斷線"),
}


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


def demo_data() -> dict[str, Any]:
    records = [
        json.loads(line)
        for line in (REPO / "data" / "samples_demo" / "bathroom_morning.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    summary = summarize(records)
    report = caregiver_report(summary, place=PLACE)
    problems = check_output(report, summary, place=PLACE)
    if problems:
        raise ValueError(f"radar-caregiver 輸出檢查沒通過：{problems}")

    notes = event_notes(summary, PLACE)
    prompts, drafts = _section(report, "【處置提示】"), _section(report, "【照護紀錄草稿】")
    events = []
    for note, prompt_line, draft in zip(notes, prompts, drafts, strict=True):
        label = note.span or f"{note.time:%H:%M}"
        if prompt_line != f"{label} {note.prompt}" or draft != note.record:
            raise ValueError(f"處置提示與事件對不上：{prompt_line}")
        if note.prompt not in KINDS:
            raise ValueError(f"build.py 的 KINDS 沒有這句處置提示，請補上：{note.prompt}")
        level, title = KINDS[note.prompt]
        events.append({
            "at": f"{note.time:%H:%M:%S}", "label": label, "level": level, "title": title,
            "prompt": note.prompt, "record": note.record,
        })

    trend = usage_trend(_load_generator().generate_two_weeks())
    trend_lines = trend_text(trend, place=PLACE)
    problems = check_trend(trend_lines, trend, place=PLACE)
    if problems or trend is None:
        raise ValueError(f"使用趨勢輸出檢查沒通過：{problems}")

    return {
        "place": PLACE,
        "day": f"{summary.online[0].start:%Y-%m-%d}",
        "from": f"{summary.online[0].start:%H:%M:%S}",
        "to": f"{summary.online[-1].end:%H:%M:%S}",
        "summary": _section(report, "【事件摘要】")[0],
        "visits": [[f"{v.start:%H:%M:%S}", f"{v.end:%H:%M:%S}"] for v in summary.visits],
        "offline": [
            [f"{a.end:%H:%M:%S}", f"{b.start:%H:%M:%S}"] for a, b in zip(summary.online, summary.online[1:], strict=False)
        ],
        "events": events,
        "trend": {
            "lines": trend_lines.splitlines(),
            "days": [
                {"day": f"{c.day.month}/{c.day.day}", "visits": c.visits, "dawn": c.dawn, "recent": i >= 7}
                for i, c in enumerate(trend.before + trend.recent)
            ],
        },
    }


def fragment(data: dict[str, Any]) -> str:
    template = (HERE / "template.html").read_text(encoding="utf-8")
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
    data = demo_data()
    if args.fragment:
        args.fragment.write_text(fragment(data), encoding="utf-8")
        print(f"寫入 {args.fragment}")
    else:
        (HERE / "prototype.html").write_text(page(data), encoding="utf-8")
        print(f"寫入 {HERE / 'prototype.html'}")
