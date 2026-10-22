"""產生三種讀者的 Dashboard 試做頁 demo/dashboard.html（**模擬資料**；docs/17_介面易用性評估.md）。

用途：評估「除了官網 Dashboard，要不要做更符合使用者便利的介面」，不是取代官網。
同一份模擬資料，依讀者切換三種畫面，每種畫面只放那位讀者最常做的事：
- 居服員：先看還有沒有要處理的警報，再看處置提示、可以複製的照護紀錄草稿、提問、兩週使用趨勢。
  句子全部來自 demo/build.py（也就是 radar-caregiver、radar-ask 的同一套函式），不另寫。
- 家屬：模擬「單人房、有長者代號 DEMO-ELDER-01」的情境，只放事件摘要、今天發生過什麼（時間與種類）
  與每日使用次數。家屬版的用語還沒擬定與審核，所以不放處置提示與照護紀錄，也不放寫著「場域統計」的趨勢句子。
  現有資料是場域資料，依 audience.py 家屬不能看；這裡是模擬的個人情境，頁面一直標明（2026-10-20 決定，F4 前不改 audience.py）。
- 系統管理人員：四台模擬裝置的狀態（在線、斷線、資料逾時、最後收到資料、紀錄筆數、無法判讀的紀錄），不看照護事件內容；
  異常裝置可以「回報問題」，回報單自動帶入遠端判斷需要的裝置資訊（diagnostics()）。

每件事分成「裝置警報」與「人員處置」兩種狀態：這份資料沒有人員處置紀錄，人員處置從「尚未確認」開始，頁面上可以示意接手與記錄處置
（只存在分頁），不從裝置訊號推論已處理（2026-10-20 使用者檢視意見）。頁面上陳述事實的句子都在 UI_TEXT，建置時跑用語檢查。

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

from radar_agent.caregiver import (
    CHANGE_AT_LEAST,
    CHANGE_RATIO,
    FULL_DAY_ONLINE,
    MIN_COUNTED_DAYS,
    offline_sentences,
)
from radar_agent.device_events import HEARTBEAT_GAP, TIMESTAMP_FORMAT, Summary, disconnected, summarize
from radar_agent.output_check import wording_problems

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
    """四台模擬裝置：(裝置名稱, 地點, 紀錄)。DEMO-SIM-01 就是原型頁的那一個上午。"""
    second = _heartbeats("radar10/DEMO-SIM-02", "08:00:00", "11:59:55", busy=("09:00:00", "09:04:00"))
    third = _heartbeats("radar10/DEMO-SIM-03", "08:00:00", "11:40:00")
    third.append({"timestamp": f"{DAY} 11:40:05", "topic": "radar10/DEMO-SIM-03", "resolution": None,
                  "payload": {"event": "offline"}})
    # 時間格式讀不懂的一筆：只計數、不參與計算（device_events._parse）
    third.insert(100, {"timestamp": f"{DAY}T09:00", "topic": "radar10/DEMO-SIM-03", "resolution": None,
                       "payload": {"event": "absence"}})
    # 10:30 之後就沒再送資料，也沒有 offline：只看自己的紀錄會以為一直在線
    fourth = _heartbeats("radar10/DEMO-SIM-04", "08:00:00", "10:30:00")
    return [("DEMO-SIM-01", "1 號廁所", morning), ("DEMO-SIM-02", "2 號廁所", second),
            ("DEMO-SIM-03", "3 號廁所", third), ("DEMO-SIM-04", "4 號廁所", fourth)]


HEARTBEAT = timedelta(seconds=5)   # 實測心跳約每 5 秒一筆（docs/05 A10）


def diagnostics(records: list[dict[str, Any]], summary: Summary) -> dict[str, Any]:
    """遠端判斷問題要的裝置層資訊；不含 payload 的事件內容（有沒有人、跌倒等照護資訊不放進回報單）。"""
    times = sorted(
        datetime.strptime(r["timestamp"], TIMESTAMP_FORMAT) for r in records
        if isinstance(r.get("timestamp"), str) and _readable(r["timestamp"]))
    gaps = sorted((b - a for a, b in zip(times, times[1:], strict=False)), reverse=True)
    biggest = max(zip(times, times[1:], strict=False), key=lambda ab: ab[1] - ab[0], default=None)
    online = sum((p.end - p.start for p in summary.online), timedelta())
    expected = int(online / HEARTBEAT) + len(summary.online)
    median = gaps[len(gaps) // 2] if gaps else None
    sample = (summary.unreadable + summary.unknown_events)[:1]
    return {
        "topic": next((str(r["topic"]) for r in records if r.get("topic")), ""),
        "expected_records": expected,
        "received_pct": round(len(times) / expected * 100) if expected else None,
        "median_gap_s": int(median.total_seconds()) if median else None,
        "max_gap": [_stamp(biggest[0]), _stamp(biggest[1]), int((biggest[1] - biggest[0]).total_seconds())] if biggest else None,
        "last_times": [_stamp(t) for t in times[-3:]],
        # 讀不懂的紀錄附一筆的形狀：時間與主題原樣（格式錯在哪），payload 只留欄位名稱，不帶有沒有人等照護資訊
        "unreadable_sample": _shape(sample[0]) if sample else None,
        "error_messages": [f"{_stamp(t)} {m}" for t, m in summary.errors][:3],
    }


def _shape(record: dict[str, Any]) -> str:
    payload = record.get("payload")
    keys = sorted(payload) if isinstance(payload, dict) else type(payload).__name__
    return json.dumps({"timestamp": record.get("timestamp"), "topic": record.get("topic"), "payload 欄位": keys},
                      ensure_ascii=False)[:200]


def _readable(stamp: str) -> bool:
    try:
        datetime.strptime(stamp, TIMESTAMP_FORMAT)
    except ValueError:
        return False
    return True


def device_rows(devices: list[tuple[str, str, list[dict[str, Any]]]]) -> tuple[str, list[dict[str, Any]]]:
    """系統管理人員看的裝置清單：只有裝置狀態，不含照護事件。

    基準時間是所有裝置裡最晚收到資料的時間（模擬用；正式版要用伺服器目前時間，否則全部裝置都停送時基準也跟著停）：最後收到資料比基準落後
    超過心跳間隔、又沒有送 offline 的裝置，判成「沒收到資料」，不能因為自己的紀錄裡沒斷線就當成在線。
    """
    summaries = [(name, place, records, summarize(records)) for name, place, records in devices]
    for name, _, _, summary in summaries:
        if not summary.online:
            raise ValueError(f"{name} 沒有讀得懂的紀錄")
    ref = max(summary.online[-1].end for *_, summary in summaries)
    rows = []
    for name, place, records, summary in summaries:
        last = summary.online[-1].end
        if summary.ended_offline is not None:
            status, since = "offline", summary.ended_offline
        elif ref - last > HEARTBEAT_GAP:
            status, since = "silent", last
        elif disconnected(summary):
            status, since = "recovered", None
        else:
            status, since = "online", None
        rows.append({
            "device": name,
            "place": place,
            "status": status,
            "last_seen": _stamp(last),
            "since": _stamp(since) if since else None,
            "minutes_without_data": int((ref - since).total_seconds() // 60) if since else None,
            "from": _stamp(summary.online[0].start),
            "online": [[_stamp(p.start), _stamp(p.end)] for p in summary.online],
            "offline": [[_stamp(a.end), _stamp(b.start)] for a, b in zip(summary.online, summary.online[1:], strict=False)],
            "records": len(records),
            "unreadable": len(summary.unreadable) + len(summary.unknown_events),
            "errors": len(summary.errors),
            "diag": diagnostics(records, summary),
        })
    return _stamp(ref), rows


# 解除事件的原因（資料裡的標題 → 事件卡上的寫法）
CAUSE = {"回覆沒事": "長者回覆沒事", "起身解除": "偵測到起身"}


def incidents(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """把一件事的警報與之後的解除合成一張事件卡；回傳 (事件卡, 配不到警報的解除)。裝置斷線另外列，不算事件。

    暫定配對：解除（resolved）配給它之前最近一則還沒解除的要到場通知（跌倒、主動呼救）。久待警報在規格裡沒有
    解除事件，所以不配；配不到的解除不丟掉，另外列出（REVIEW.md 3.1）。兩件要到場的通知重疊時可能配錯，
    正式版要有事件序號（會議清單 A11）。這份資料沒有人員處置紀錄：人員處置一律從「尚未確認」開始。
    """
    out: list[dict[str, Any]] = []
    orphans: list[dict[str, Any]] = []
    open_urgent: list[dict[str, Any]] = []
    for e in events:
        if e["level"] in ("urgent", "check"):
            card = {"id": f"e{len(out) + 1}", "at": e["at"], "label": e["label"], "level": e["level"],
                    "title": e["title"], "prompt": e["prompt"], "cleared": None}
            out.append(card)
            if e["level"] == "urgent":
                open_urgent.append(card)
        elif e["level"] == "resolved":
            if open_urgent:
                open_urgent.pop()["cleared"] = {"label": e["label"], "cause": CAUSE.get(e["title"], e["title"])}
            else:
                orphans.append({"label": e["label"], "title": e["title"]})
    return out, orphans


# 頁面上所有固定句型：JS 只把 {n}、{time} 這類欄位換成資料裡的數字與時間，不自己寫句子。
# 建置時每一句都要通過 wording_problems（不用醫療用語、不寫推測）。
UI_TEXT: dict[str, str] = {
    "tag": "模擬歷史資料",
    "head_pending": "有 {n} 件待接手",
    "head_none": "沒有待接手的事件",
    "no_clear": "{n} 件未收到裝置解除紀錄，狀態待確認：{list}",
    "local_note": "這份資料本身沒有人員處置紀錄；下面的接手與處置只存在這個分頁，重新整理就會清掉。",
    "count_pending": "待接手 {n} 件",
    "count_handling": "處理中 {n} 件",
    "count_done": "已完成 {n} 件",
    "pill_urgent": "在線期間立即到場通知：{n} 件",
    "pill_check": "在線期間前往查看通知：{n} 件",
    "pill_offline": "裝置斷線：{n} 段",
    "prompt_then": "當時通知：{prompt}",
    "device_cleared": "{time} {cause}，裝置警報解除",
    "device_unknown": "未收到解除紀錄，狀態待確認",
    "human_pending": "尚未確認",
    "human_handling": "處理中：{who} {time} 接手",
    "human_done": "已完成：{result}（{who}，{time}）",
    "family_handling": "處理中（{time} 接手）",
    "family_done": "已完成：{result}（{time}）",
    "family_note": "這份資料本身沒有人員處置紀錄，所以先顯示「尚未確認」；居服員在這一頁示意接手或記錄處置後，這裡會同步顯示。",
    "orphan": "{time} {title}：找不到對應的警報",
    "need_who": "請填處理人代號。",
    "need_time": "請填時間。",
    "need_result": "請選擇處置結果。",
    "saved": "已儲存（只存在這個分頁）。",
    "admin_head": "{n} 台裝置異常",
    "admin_none": "沒有異常裝置",
    "admin_offline": "{time} 收到斷線訊息，之後 {min} 分鐘沒收到資料",
    "admin_silent": "最後收到資料 {time}，之後 {min} 分鐘沒收到資料，也沒有收到斷線訊息",
    "admin_unreadable": "無法判讀的紀錄 {n} 筆",
    "admin_errors": "裝置錯誤 {n} 次",
    "admin_online": "目前在線 {n} 台，其中 {m} 台曾斷線",
    "admin_offline_n": "斷線中 {n} 台",
    "admin_silent_n": "資料逾時 {n} 台",
    "admin_base": "基準時間 {time}（模擬：所有裝置最晚收到資料的時間；正式版用伺服器目前時間）",
    "status_online": "在線",
    "status_recovered": "在線（曾斷線）",
    "status_offline": "斷線中",
    "status_silent": "資料逾時",
    # 頁面上其他陳述事實的句子（#74 審查 N1）
    "period_visits": "在線期間有人使用 {n} 次",
    "counted_days": "這兩週可比較的天數：前一週 {a} 天、近 7 天 {b} 天。",
    "copied": "已複製 {n} 行。",
    "copy_fallback": "已選取，請按 Ctrl+C 複製。",
    "ask_hint": "選一題看答案。",
    "admin_row": "{device}（{place}）：{facts}。",
    "since": "{time} 起",
    "human_taken": "{who} {time} 接手",
    "label_who": "處理人代號（英數字，例如 CW-01，不填姓名）",
    "label_take_time": "接手時間（{day}）",
    "label_done_time": "處置時間（{day}）",
    "label_note": "備註（選填，最多 200 字，不寫長者或人員姓名）",
    "need_code": "處理人代號只能用英文字母、數字與「-」，最多 20 字。",
    "too_early_event": "時間不能早於事件發生的 {time}。",
    "too_early_taken": "處置時間不能早於接手時間 {time}。",
    # 裝置問題回報：系統自動整理遠端判斷需要的裝置資訊，管理員只補描述（2026-10-22 使用者：Demo 用，說明 UI 能自動傳達足夠資訊）
    "report_title": "回報給技術支援窗口（待確認）",
    "report_intro": "下面是系統自動整理的裝置資訊，不含照護事件與長者資料；補上描述就能送出。",
    "r_head": "【裝置問題回報】{device}（{place}）",
    "r_status": "狀態：{status}。判斷依據：{basis}",
    "r_basis_offline": "{time} 收到斷線訊息（由 broker 發出），之後沒有再收到資料",
    "r_basis_silent": "最後收到資料 {time}，之後超過心跳間隔（30 秒）沒有資料，也沒有收到斷線訊息",
    "r_basis_unreadable": "裝置在線，但有無法判讀的紀錄",
    "r_base": "基準時間：{ref}；距最後收到資料 {min} 分鐘",
    "r_topic": "裝置主題：{topic}",
    "r_online": "這段時間的在線時段：{spans}",
    "r_records": "紀錄：收到 {n} 筆，在線期間預期約 {e} 筆（每 5 秒一筆）；心跳間隔中位數 {m} 秒；最大間隔 {g} 秒（{a}–{b}）",
    "r_last": "最後三筆紀錄時間：{times}",
    "r_unreadable": "無法判讀的紀錄 {n} 筆，第一筆的形狀（payload 只留欄位名稱）：{sample}",
    "r_errors": "裝置錯誤 {n} 次：{list}",
    "r_peers": "同時段其他裝置：{list}",
    "r_urgency": "急迫程度：{u}",
    "r_checked": "現場已檢查：{list}",
    "r_desc": "管理員描述：{text}",
    "urgency_care": "影響照護，請盡快處理",
    "urgency_later": "可排程處理",
    "checked_none": "尚未檢查",
    "label_desc": "問題描述（不寫長者或人員姓名）",
    "need_desc": "請填問題描述。",
    "report_sent": "已送出（示意）：回報單 {id}，等待技術支援回覆。這一頁不會真的寄出。",
}
# 記錄處置的結果選項：只描述做了什麼，不判斷長者狀況（判斷寫在備註，由人負責）
RESULTS = ["已到場確認", "已到場協助", "以電話或語音確認", "其他（見備註）"]
# 家屬看不到備註：「其他（見備註）」在家屬畫面改寫成「其他處置」（#74 審查 N2）
FAMILY_RESULTS = {"其他（見備註）": "其他處置"}


def check_ui_text() -> None:
    texts = [*UI_TEXT.items(), *((f"result{i}", r) for i, r in enumerate(RESULTS)), *FAMILY_RESULTS.items()]
    for key, text in texts:
        problems = wording_problems(text)
        if problems:
            raise ValueError(f"頁面固定句型「{key}」沒通過用語檢查：{problems}")


def _period(data: dict[str, Any]) -> dict[str, Any]:
    first, last = data["from"], data["to"]
    return {"day": f"{int(first[5:7])}/{int(first[8:10])}", "from": first[11:16], "to": last[11:16],
            "visits": len(data["visits"])}


def trend_rule() -> str:
    """趨勢的判定方式：照 caregiver.py 的常數寫，常數改了這句跟著變。"""
    return (f"判定方式：兩週各至少要有 {MIN_COUNTED_DAYS} 天可比較（當天在線 {int(FULL_DAY_ONLINE.total_seconds() // 3600)} 小時以上）；"
            f"平均每日相差 {CHANGE_AT_LEAST} 次以上，而且每日次數相差前一週的 {int(CHANGE_RATIO['visits'] * 100)}%"
            f"（凌晨 {int(CHANGE_RATIO['dawn'] * 100)}%）以上，才寫「增加」或「減少」，否則寫「差不多」。")


def dashboard_data() -> dict[str, Any]:
    build = _load_build()
    morning = build._jsonl(REPO / "data" / "samples_demo" / "bathroom_morning.jsonl")
    summary = summarize(morning)
    caregiver = build.demo_data(place=CAREGIVER_PLACE)
    days = caregiver["trend"]["days"]
    caregiver.update({
        "period": _period(caregiver),
        "incidents": incidents(caregiver["events"])[0],
        "orphans": incidents(caregiver["events"])[1],
        "offline_sentences": offline_sentences(summary),
        "trend_rule": trend_rule(),
        "counted_days": [sum(d["visits"] is not None for d in days[:7]), sum(d["visits"] is not None for d in days[7:])],
    })
    family_full = build.demo_data(place=FAMILY_PLACE)
    family = {
        "source": "sim",  # 家屬畫面只能用模擬資料：現有資料是場域資料，依 audience.py 家屬不能看
        "elder": FAMILY_ELDER,
        "place": FAMILY_PLACE,
        "period": _period(family_full),
        "from": family_full["from"],
        "to": family_full["to"],
        "summary": family_full["summary"],
        "visits": family_full["visits"],
        "offline": family_full["offline"],
        "offline_sentences": offline_sentences(summary),
        # 家屬只看「什麼時候、發生什麼、裝置警報與人員處置的狀態」：處置提示與照護紀錄是給居服員的
        # 事件 id 和居服員畫面相同：同一個模擬上午，居服員示意的處置會同步到這裡
        "incidents": [{"id": c["id"], "at": c["at"], "label": c["label"], "level": c["level"], "title": c["title"],
                       "cleared": c["cleared"]} for c in incidents(family_full["events"])[0]],
        "days": family_full["trend"]["days"],
    }
    ref, devices = device_rows(simulated_devices(morning))
    check_ui_text()
    return {"caregiver": caregiver, "family": family, "devices": devices, "devices_ref": ref,
            "text": UI_TEXT, "results": RESULTS, "family_results": FAMILY_RESULTS}


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
