"""產生流程確認頁 demo/flow_check.html（**模擬資料**；內部確認「Dashboard 要資料 → 本 repo 產生文字 → 傳回顯示」）。

測試看板暫代官網 Dashboard，讓內部先確認流程走得通；正式畫面由官網 Dashboard 做（docs/16「第一階段的流程」）。
每個情境依序列出四步：
  ① Dashboard 送出的請求（API 是草案，還沒和 Dashboard 約定）
  ② Dashboard 那一側在範圍內有的資料（筆數、第一筆與最後一筆的時間）
  ③ 本 repo 回傳的 JSON：就是 `radar-caregiver --json --from --to` 的輸出，原樣放進頁面
  ④ Dashboard 照原文顯示的樣子（docs/16 的畫面建議）
③ 和 ④ 對不上、或資訊不夠確定的地方，頁面列成「待決」，不在這裡改輸出格式（單向門「給讀者的輸出與用語」）。

重新產生（tests/test_flow_check.py 會檢查）：
    python demo/flow_build.py
實測資料版（先用 radar-field-data mask 遮罩；寫到 outputs/flow_field.html，不會被 commit，也不發布）：
    python demo/flow_build.py --events outputs/field/FIELD-01.jsonl --place "1 號廁所" \\
        --range "2026-10-22 07:00" "2026-10-22 20:00"
"""
from __future__ import annotations

import argparse
import contextlib
import html
import io
import json
from pathlib import Path
from typing import Any

from radar_agent.caregiver import main as caregiver_main
from radar_agent.device_events import HEARTBEAT_GAP, parse_timestamp, read_records
from radar_agent.field_data import MAC_LIKE, check_out_dir
from radar_agent.time_range import in_range, parse_bound

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SAMPLE = REPO / "data" / "samples_demo" / "bathroom_morning.jsonl"
SAMPLE_PLACE = "1 號廁所"
OUT = HERE / "flow_check.html"
FIELD_OUT = REPO / "outputs" / "flow_field.html"

# 模擬上午（08:00–12:00，11:10–11:25 斷線）的四個情境：名稱、開始、結束
SCENARIOS = [
    ("一段上午", "2026-10-14 08:00", "2026-10-14 10:00"),
    ("跨過斷線", "2026-10-14 10:30", "2026-10-14 12:00"),
    ("要的範圍比資料長", "2026-10-14 11:00", "2026-10-14 14:00"),
    ("範圍內沒有資料", "2026-10-14 13:00", "2026-10-14 15:00"),
]

# ④ 的 Alerts 欄依 level 上色（docs/16：urgent 立即到場、check 前往查看、resolved 警報解除、device 裝置問題）
LEVELS = ("urgent", "check", "resolved", "device")


def run_caregiver(events: Path, place: str, start: str, end: str) -> str:
    """跑 radar-caregiver 的同一個 main，回傳它印出的 JSON 原文；輸出檢查沒過就停下來。"""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = caregiver_main([str(events), "--place", place, "--json", "--from", start, "--to", end])
    if code != 0:
        raise ValueError(f"radar-caregiver 沒有產生輸出（{start}–{end}）：{err.getvalue().strip()}")
    return out.getvalue()


def data_side(records: list[dict[str, Any]], start: str, end: str) -> dict[str, Any]:
    """② Dashboard 那一側：範圍內有幾筆、第一筆與最後一筆的時間。"""
    kept = in_range(records, parse_bound(start), parse_bound(end))
    stamps = sorted(str(r["timestamp"]) for r in kept if parse_timestamp(r.get("timestamp")) is not None)
    return {"count": len(kept), "first": stamps[0] if stamps else None, "last": stamps[-1] if stamps else None}


def pending(report: dict[str, Any], side: dict[str, Any], end: str) -> list[str]:
    """③ 的內容不夠讓 Dashboard 確定的地方。只列出來，不在這裡改輸出格式（單向門，待人決定）。"""
    notes = []
    counts = report["counts"]
    if "無法判斷" in report["summary"] and counts["fall_alerts"] == 0:
        notes.append("摘要寫「無法判斷」，數字欄位卻是 0：Dashboard 做成數字卡會顯示「跌倒警報 0」。"
                     "數字要能表示「無法判斷」（例如 null），屬於給讀者的輸出格式，待決。")
    if side["count"] and report["from"] is None:
        notes.append(f"Dashboard 有 {side['count']} 筆紀錄，回傳的文字卻是「沒有收到資料」：本 repo 讀不懂這種格式"
                     "（例如 Radar60FL，docs/05 A45）。居服員會以為裝置沒送資料，要先確認格式。")
    last = side["last"]
    if last is not None and parse_bound(end) - parse_bound(last) > HEARTBEAT_GAP:
        notes.append(f"要的是到 {end[11:]}，資料只到 {last[11:]}；回傳的文字沒有寫出之後沒有資料，"
                     "Dashboard 也分不出是沒有事件還是資料還沒到。回傳要附資料到哪裡為止（例如 data_until），待決。")
    return notes


def scenario(records: list[dict[str, Any]], events: Path, device: str, place: str,
             name: str, start: str, end: str) -> dict[str, Any]:
    raw = run_caregiver(events, place, start, end)
    report = json.loads(raw)
    side = data_side(records, start, end)
    iso = lambda t: parse_bound(t).strftime("%Y-%m-%dT%H:%M:%S")  # noqa: E731
    return {
        "name": name, "start": start, "end": end, "place": place, "device": device,
        "request": f"GET /api/v1/devices/{device}/records?from={iso(start)}&to={iso(end)}",
        "command": f'radar-caregiver {events.relative_to(REPO) if events.is_relative_to(REPO) else events.name} '
                   f'--place "{place}" --json --from "{start}" --to "{end}"',
        "side": side, "raw": raw, "report": report, "pending": pending(report, side, end),
    }


def _e(text: object) -> str:
    return html.escape(str(text))


def _step(n: str, title: str, body: str) -> str:
    return f'<section class="step"><h3><span class="n">{n}</span>{title}</h3>{body}</section>'


def board(s: dict[str, Any]) -> str:
    """④ 仿官網 Dashboard 的 Devices 頁：左邊 Alerts 放處置提示，右邊裝置卡片放事件摘要，點開看照護紀錄草稿。

    句子全部照 ③ 的原文，不另寫；數字卡不放（摘要寫「無法判斷」時數字仍是 0，見 docs/16「流程確認頁」）。
    沒有事件但摘要寫「無法判斷」時，Alerts 放摘要，不寫「沒有待處理的事件」。
    """
    r = s["report"]
    alerts = [f'<li class="lv-{e["level"] if e["level"] in LEVELS else "device"}"><b>{_e(e["label"])}</b> '
              f'{_e(r["place"])}：{_e(e["prompt"])}</li>' for e in r["events"]]
    if not alerts:
        alerts = ([f'<li class="lv-device">{_e(r["summary"])}</li>'] if "無法判斷" in r["summary"]
                  else ['<li class="lv-none">沒有待處理的事件。</li>'])
    until = f'資料到 {r["to"][5:10].replace("-", "/")} {r["to"][11:16]}' if r["to"] else "沒有收到資料"
    detail = "".join(f'<li><b>{_e(e["label"])}</b> {_e(e["prompt"])}<br><span class="hint">照護紀錄草稿：{_e(e["record"])}</span></li>'
                     for e in r["events"])
    card = (f'<div class="device"><p class="code">{_e(s["device"])}</p><p class="name">{_e(r["place"])}</p>'
            f'<p class="summary">{_e(r["summary"])}</p><p class="hint">{_e(until)}</p>'
            + (f'<details><summary>處置提示與照護紀錄草稿</summary><ul>{detail}</ul></details>' if detail else "")
            + "</div>")
    return (f'<div class="board"><section aria-label="Alerts"><h4>Alerts</h4><ul class="alerts">{"".join(alerts)}</ul></section>'
            f'<section aria-label="Devices"><h4>Devices</h4>{card}</section></div>')


def render(s: dict[str, Any], i: int) -> str:
    side = s["side"]
    req = (f'<p>要 {_e(s["start"])} 到 {_e(s["end"])}（含開始、不含結束）的資料。</p>'
           f'<pre tabindex="0" aria-label="請求">{_e(s["request"])}</pre>'
           f'<p class="hint">本 repo 目前用這個指令處理同一段時間：</p><pre tabindex="0" aria-label="指令">{_e(s["command"])}</pre>')
    got = (f'<p>範圍內有 <b>{side["count"]}</b> 筆紀錄。</p>'
           + (f'<p>第一筆 {_e(side["first"])}<br>最後一筆 {_e(side["last"])}</p>' if side["first"] else "<p>範圍內沒有紀錄。</p>"))
    out = f'<pre tabindex="0" aria-label="回傳的 JSON" class="json">{_e(s["raw"].rstrip())}</pre>'
    shown = board(s)
    notes = "".join(f"<li>{_e(n)}</li>" for n in s["pending"])
    todo = f'<div class="pending"><h3>待決</h3><ul>{notes}</ul></div>' if notes else ""
    return (f'<article id="s{i}"><h2>情境 {i}：{_e(s["name"])}<small>{_e(s["start"])} – {_e(s["end"][11:])}</small></h2>'
            f'<div class="flow">{_step("①", "Dashboard 送出請求（草案）", req)}{_step("②", "Dashboard 有的資料", got)}'
            f'{_step("③", "本 repo 回傳（radar-caregiver --json）", out)}</div>'
            f'{_step("④", "Dashboard 照原文顯示（仿 Devices 頁版面，示意）", shown)}{todo}</article>')


STYLE = """
:root{--bg:#f4f6f6;--card:#fff;--ink:#16232a;--mute:#56676f;--line:#c9d2d5;--teal:#1c6a72;--warn:#8a5a00;--warnbg:#fdf3dc;--ban:#2b2233;--urgent:#b42318}
@media (prefers-color-scheme:dark){:root{--bg:#121819;--card:#1b2325;--ink:#e6eef0;--mute:#9fb0b6;--line:#34444a;--teal:#7cc4cc;--warn:#f0c46a;--warnbg:#2d2614;--ban:#3a2f45;--urgent:#f97066}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 "Noto Sans TC","Microsoft JhengHei",sans-serif}
.banner{background:var(--ban);color:#fff;padding:10px 16px;font-weight:700}
main{max-width:1240px;margin:0 auto;padding:20px 16px 40px}
h1{font-size:22px;margin:0 0 6px} .lead{color:var(--mute);margin:0 0 12px}
nav a{display:inline-block;margin:0 8px 8px 0;padding:6px 12px;border:1px solid var(--line);border-radius:999px;color:var(--teal);text-decoration:none;background:var(--card)}
article{margin-top:28px}
h2{font-size:18px;margin:0 0 10px} h2 small{font-weight:400;color:var(--mute);margin-left:10px;font-size:14px}
.flow{display:grid;grid-template-columns:1fr 0.8fr 1.6fr;gap:10px;margin-bottom:10px}
.step{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;min-width:0}
.step h3{font-size:14px;margin:0 0 8px;color:var(--teal)} .n{margin-right:6px}
.step p{margin:0 0 8px}
pre{margin:0 0 8px;padding:8px;background:var(--bg);border-radius:6px;font:12px/1.5 Consolas,monospace;white-space:pre-wrap;word-break:break-all}
pre.json{max-height:420px;overflow:auto;white-space:pre}
pre:focus-visible,nav a:focus-visible{outline:3px solid var(--teal);outline-offset:2px}
.hint{color:var(--mute);font-size:13px}
.board{display:grid;grid-template-columns:1fr 1.2fr;gap:16px;background:var(--bg);border-radius:8px;padding:12px}
.board h4{margin:0 0 8px;font-size:15px}
.alerts{list-style:none;margin:0;padding:0}
.alerts li{border-left:4px solid var(--line);background:var(--card);border-radius:6px;padding:6px 10px;margin-bottom:6px;font-size:14px}
.alerts .lv-urgent{border-left-color:var(--urgent)} .alerts .lv-check{border-left-color:var(--warn)}
.alerts .lv-resolved{border-left-color:var(--teal)} .alerts .lv-device{border-left-color:var(--mute)} .alerts .lv-none{color:var(--mute)}
.device{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}
.device p{margin:0 0 6px} .code{font-weight:700;color:var(--teal);font-size:13px} .name{font-weight:700;font-size:17px}
.summary{border-left:4px solid var(--teal);padding-left:8px}
.device ul{margin:6px 0 0;padding-left:18px;font-size:14px}
details summary{cursor:pointer;color:var(--teal)}
details summary:focus-visible{outline:3px solid var(--teal);outline-offset:2px}
.pending{margin-top:10px;background:var(--warnbg);border:1px solid var(--warn);border-radius:10px;padding:10px 14px}
.pending h3{margin:0 0 4px;font-size:14px;color:var(--warn)} .pending ul{margin:0;padding-left:18px}
@media (max-width:960px){.flow{grid-template-columns:1fr 1fr}}
@media (max-width:560px){.flow,.board{grid-template-columns:1fr}}
"""


def page(scenarios: list[dict[str, Any]], *, field: bool = False) -> str:
    banner = ("實測資料（遮罩後）：只在本機看，不要 commit、不要發布" if field
              else "模擬資料：非真實裝置紀錄・內部確認流程用・API 是草案，還沒和 Dashboard 約定")
    nav = "".join(f'<a href="#s{i}">情境 {i}：{_e(s["name"])}</a>' for i, s in enumerate(scenarios, 1))
    body = "".join(render(s, i) for i, s in enumerate(scenarios, 1))
    return ('<!doctype html>\n<html lang="zh-Hant">\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n<title>流程確認</title>\n'
            f"<style>{STYLE}</style>\n<div class=\"banner\" role=\"note\">{banner}</div>\n<main>\n"
            "<h1>流程確認：Dashboard 要資料 → 本 repo 產生文字 → 傳回顯示</h1>\n"
            '<p class="lead">測試看板暫代官網 Dashboard，確認同一段時間的資料、回傳的文字與畫面對得上。'
            "③ 是轉換程式的原始輸出；④ 照原文顯示，不改寫。</p>\n"
            f"<nav aria-label=\"情境\">{nav}</nav>\n{body}\n</main>\n</html>\n")


def build(events: Path = SAMPLE, place: str = SAMPLE_PLACE,
          ranges: list[tuple[str, str, str]] = SCENARIOS) -> list[dict[str, Any]]:
    records = read_records(events)
    device = str(next((r["topic"] for r in records if r.get("topic")), "")).split("/")[1:2] or ["（沒有資料）"]
    return [scenario(records, events, device[0], place, *r) for r in ranges]


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--events", type=Path, help="改用遮罩過的實測資料（radar-field-data mask 的輸出）")
    ap.add_argument("--place", default=SAMPLE_PLACE, help="地點名稱；不放長者姓名等個人資料")
    ap.add_argument("--range", nargs=2, action="append", metavar=("FROM", "TO"), help="實測資料版的時間範圍，可以給多次")
    args = ap.parse_args()
    if args.events:
        if MAC_LIKE.search(args.events.read_text(encoding="utf-8")):
            raise SystemExit(f"錯誤：{args.events} 裡還有像 MAC 的字串，請先用 radar-field-data mask 遮罩")
        if not args.range:
            raise SystemExit("錯誤：實測資料版要用 --range 指定時間範圍")
        problem = check_out_dir(FIELD_OUT.parent)
        if problem:
            raise SystemExit(f"錯誤：{problem}")
        ranges = [(f"{a}–{b[11:] if b[:10] == a[:10] else b}", a, b) for a, b in args.range]
        FIELD_OUT.parent.mkdir(exist_ok=True)
        FIELD_OUT.write_text(page(build(args.events.resolve(), args.place, ranges), field=True), encoding="utf-8")
        print(f"寫入 {FIELD_OUT}（實測資料，不要 commit、不要發布）")
    else:
        OUT.write_text(page(build()), encoding="utf-8")
        print(f"寫入 {OUT}")
