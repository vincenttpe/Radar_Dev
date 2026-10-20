"""產生三種讀者的 Dashboard 試做頁 demo/dashboard.html（**模擬資料**；docs/17_介面易用性評估.md）。

用途：評估「除了官網 Dashboard，要不要做更符合使用者便利的介面」，不是取代官網。
同一份模擬資料，依讀者切換三種畫面，每種畫面只放那位讀者最常做的事：
- 居服員：先看還有沒有要處理的警報，再看處置提示、可以複製的照護紀錄草稿、提問、兩週使用趨勢。
  句子全部來自 demo/build.py（也就是 radar-caregiver、radar-ask 的同一套函式），不另寫。
- 家屬：模擬「單人房、有長者代號 DEMO-ELDER-01」的情境，只放事件摘要、今天發生過什麼（時間與種類）
  與每日使用次數。家屬版的用語還沒擬定與審核，所以不放處置提示與照護紀錄，也不放寫著「場域統計」的趨勢句子。
  現有資料是場域資料，依 audience.py 家屬不能看；這裡是模擬的個人情境，頁面一直標明（2026-10-20 決定，F4 前不改 audience.py）。
- 系統管理人員：三台模擬裝置的狀態（在線、斷線、最後收到資料、紀錄筆數、無法判讀的紀錄），不看照護事件內容。

重新產生（tests/test_dashboard.py 會檢查）：
    python demo/dashboard_build.py                  # → demo/dashboard.html
    python demo/dashboard_build.py --fragment 路徑  # 不含 doctype 的版本，給 Artifact 發布用
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

from radar_agent.caregiver import device_problem_sentences
from radar_agent.device_events import Summary, disconnected, summarize

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PLACEHOLDER = "/*DASHBOARD_DATA*/null"
CAREGIVER_PLACE = "1 號廁所"
FAMILY_ELDER = "DEMO-ELDER-01"
FAMILY_PLACE = f"{FAMILY_ELDER} 房間的浴廁"
DAY = "2026-10-14"


def _load_build() -> ModuleType:
    spec = importlib.util.spec_from_file_location("demo_build", HERE / "build.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stamp(t: datetime) -> str:
    return f"{t:%Y-%m-%d %H:%M:%S}"


def _heartbeats(topic: str, start: str, end: str, busy: tuple[str, str] | None = None) -> list[dict[str, Any]]:
    """每 5 秒一筆（docs/05 A10）；busy 期間有人走動，其餘沒人。"""
    t, stop = datetime.fromisoformat(f"{DAY} {start}"), datetime.fromisoformat(f"{DAY} {end}")
    lo, hi = (datetime.fromisoformat(f"{DAY} {b}") for b in busy) if busy else (None, None)
    out = [{"timestamp": _stamp(t), "topic": topic, "resolution": None, "payload": {"event": "online"}}]
    while (t := t + timedelta(seconds=5)) <= stop:
        event = "moving" if lo is not None and hi is not None and lo <= t < hi else "absence"
        payload: dict[str, Any] = {"mode": "radar", "trigger": "track", "userReply": False, "event": event}
        out.append({"timestamp": _stamp(t), "topic": topic, "resolution": None, "payload": payload})
    return out


def simulated_devices(morning: list[dict[str, Any]]) -> list[tuple[str, str, list[dict[str, Any]]]]:
    """三台模擬裝置：(裝置名稱, 地點, 紀錄)。DEMO-SIM-01 就是原型頁的那一個上午。"""
    second = _heartbeats("radar10/DEMO-SIM-02", "08:00:00", "11:59:55", busy=("09:00:00", "09:04:00"))
    third = _heartbeats("radar10/DEMO-SIM-03", "08:00:00", "11:40:00")
    third.append({"timestamp": f"{DAY} 11:40:05", "topic": "radar10/DEMO-SIM-03", "resolution": None,
                  "payload": {"event": "offline"}})
    # 時間格式讀不懂的一筆：只計數、不參與計算（device_events._parse）
    third.insert(100, {"timestamp": f"{DAY}T09:00", "topic": "radar10/DEMO-SIM-03", "resolution": None,
                       "payload": {"event": "absence"}})
    return [("DEMO-SIM-01", "1 號廁所", morning), ("DEMO-SIM-02", "2 號廁所", second),
            ("DEMO-SIM-03", "3 號廁所", third)]


def device_row(name: str, place: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    """系統管理人員看的一列：只有裝置狀態，不含照護事件。"""
    summary: Summary = summarize(records)
    if not summary.online:
        raise ValueError(f"{name} 沒有讀得懂的紀錄")
    if summary.ended_offline is not None:
        status = "offline"
    elif disconnected(summary):
        status = "recovered"
    else:
        status = "online"
    last = summary.online[-1].end
    return {
        "device": name,
        "place": place,
        "status": status,
        "last_seen": _stamp(last),
        "from": _stamp(summary.online[0].start),
        "online": [[_stamp(p.start), _stamp(p.end)] for p in summary.online],
        "offline": [[_stamp(a.end), _stamp(b.start)] for a, b in zip(summary.online, summary.online[1:], strict=False)],
        "ended_offline": _stamp(summary.ended_offline) if summary.ended_offline else None,
        "records": len(records),
        "unreadable": len(summary.unreadable) + len(summary.unknown_events),
        "errors": len(summary.errors),
        "problems": device_problem_sentences(summary),
    }


def dashboard_data() -> dict[str, Any]:
    build = _load_build()
    caregiver = build.demo_data(place=CAREGIVER_PLACE)
    family_full = build.demo_data(place=FAMILY_PLACE)
    family = {
        "elder": FAMILY_ELDER,
        "place": FAMILY_PLACE,
        "from": family_full["from"],
        "to": family_full["to"],
        "summary": family_full["summary"],
        "visits": family_full["visits"],
        "offline": family_full["offline"],
        # 家屬只看「什麼時候、發生什麼」：處置提示與照護紀錄是給居服員的
        "events": [{"at": e["at"], "label": e["label"], "level": e["level"], "title": e["title"]}
                   for e in family_full["events"]],
        "days": family_full["trend"]["days"],
    }
    morning = build._jsonl(REPO / "data" / "samples_demo" / "bathroom_morning.jsonl")
    devices = [device_row(name, place, records) for name, place, records in simulated_devices(morning)]
    return {"caregiver": caregiver, "family": family, "devices": devices}


def fragment(data: dict[str, Any]) -> str:
    template = (HERE / "dashboard_template.html").read_text(encoding="utf-8")
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
    data = dashboard_data()
    if args.fragment:
        args.fragment.write_text(fragment(data), encoding="utf-8")
        print(f"寫入 {args.fragment}")
    else:
        (HERE / "dashboard.html").write_text(page(data), encoding="utf-8")
        print(f"寫入 {HERE / 'dashboard.html'}")
