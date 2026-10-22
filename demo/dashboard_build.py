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
  本場域的維修紀錄（回報單狀態與處理結果）也在這裡。
- 維運後台（團隊或廠商）：跨場域的維修統計與給韌體、硬體更新的參考（ops_data()），只用場域代號與裝置代號。

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
from itertools import pairwise
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
from radar_agent.device_events import (
    HEARTBEAT_GAP,
    TIMESTAMP_FORMAT,
    Summary,
    disconnected,
    summarize,
)
from radar_agent.output_check import wording_problems

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PLACEHOLDER = "/*DASHBOARD_DATA*/null"
REGISTRY = HERE / "device_registry.json"   # 模擬的裝置設定紀錄：裝置代號 → 安裝地點與設定變更
CARE_DEVICE = "DEMO-SIM-01"                  # 居服員與家屬畫面用的模擬上午來自這台（data/samples_demo/bathroom_morning.jsonl）
MAINTENANCE = HERE / "maintenance_sim.json"  # 模擬的裝置清單與維修紀錄（維運後台用）
OWN_SITE = "SITE-A"                          # 這一頁的系統管理人員所在的場域：只看得到自己場域的維修紀錄
CATEGORY = {"offline": "斷線", "silent": "資料逾時", "unreadable": "無法判讀", "error": "裝置錯誤"}
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


def load_registry(path: Path = REGISTRY) -> dict[str, dict[str, Any]]:
    """裝置設定紀錄：{裝置代號: {place, area, use, history}}。居服員、系統管理人員畫面與回報單上的地點都從這裡查。

    人工維護的檔案，建置時先檢查：欄位齊全、至少一筆變更紀錄且照時間排、同一台不能登記兩次。
    安裝與移機的紀錄帶 place，回報單依回報當時查地點（place_at）；第一筆一定要有地點，最後一次設定的地點要等於現在的地點。
    """
    entries = json.loads(path.read_text(encoding="utf-8"))["devices"]
    registry: dict[str, dict[str, Any]] = {}
    for e in entries:
        missing = [k for k in ("device", "place", "area", "use", "history") if not e.get(k)]
        if missing:
            raise ValueError(f"裝置設定紀錄缺欄位或是空的：{e.get('device', '?')} {missing}")
        if any(not all(h.get(k) for k in ("at", "by", "change")) for h in e["history"]):
            raise ValueError(f"裝置設定紀錄的變更紀錄缺欄位：{e['device']}")
        if [h["at"] for h in e["history"]] != sorted(h["at"] for h in e["history"]):
            raise ValueError(f"裝置設定紀錄的變更紀錄沒有照時間排：{e['device']}")
        set_places = [h["place"] for h in e["history"] if h.get("place")]
        if not e["history"][0].get("place") or set_places[-1] != e["place"]:
            raise ValueError(f"裝置設定紀錄的地點和變更紀錄對不上：{e['device']}")
        if e["device"] in registry:
            raise ValueError(f"裝置設定紀錄重複登記：{e['device']}")
        registry[e["device"]] = e
    return registry


TICKET_FIELDS = ("id", "device", "category", "fw", "reported", "accepted", "resolved", "outcome",
                 "action", "part", "fw_after", "downtime_min", "handler")


def load_maintenance(path: Path = MAINTENANCE) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """(裝置清單, 回報單)。裝置清單的韌體是現在的版本；回報單記的是當時的版本。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    fleet = [dict(zip(("device", "site", "batch", "fw"), row, strict=True)) for row in raw["fleet"]]
    tickets = [dict(zip(TICKET_FIELDS, row, strict=True)) for row in raw["tickets"]]
    sites = {f["device"]: f for f in fleet}
    for t in tickets:
        t["site"], t["batch"] = sites[t["device"]]["site"], sites[t["device"]]["batch"]
    return fleet, tickets


def _hours(a: str, b: str) -> float:
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 3600


REPEAT_AT_LEAST = 2     # 同一台回報幾次以上列為「反覆出問題」
FINDING_AT_LEAST = 3    # 參考清單：至少幾件才列
FINDING_RATIO = 2       # 韌體：每台件數要超過其他版本的幾倍
SAME_ACTION_AT_LEAST = 2  # 同一類問題至少幾件已結案、都用同一種方式處理，才列入


def _median(values: list[float]) -> float | None:
    values = sorted(values)
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def _per_device(n: int, devices: int) -> str:
    return f"{n / devices:.2f}" if devices else "—"


def ops_data(fleet: list[dict[str, str]], tickets: list[dict[str, Any]]) -> dict[str, Any]:
    """維運後台：跨場域的維修統計與給韌體、硬體更新的參考。只用場域代號與裝置代號。

    韌體的分母是「用過這個版本的裝置」：現在是這一版的，加上回報單上記載曾經是這一版、後來更新的。
    參考清單是固定規則找出來的「值得檢查」，不是原因判斷，只放給韌體與硬體的項目；反覆回報的裝置另外列表（repeat）。
    門檻寫在檔案前面的常數，頁面上的列入規則照常數產生；改了要一起改 docs/17。
    """
    fw_devices: dict[str, set[str]] = {}
    for f in fleet:
        fw_devices.setdefault(f["fw"], set()).add(f["device"])
    for t in tickets:
        fw_devices.setdefault(t["fw"], set()).add(t["device"])
    batch_devices: dict[str, set[str]] = {}
    for f in fleet:
        batch_devices.setdefault(f["batch"], set()).add(f["device"])

    by_category: list[dict[str, Any]] = []
    for key, label in CATEGORY.items():
        rows = [t for t in tickets if t["category"] == key]
        done = [t for t in rows if t["resolved"]]
        by_category.append({"key": key, "label": label, "n": len(rows),
                            "median_h": _median([_hours(t["reported"], t["resolved"]) for t in done]),
                            "actions": sorted({t["action"] for t in done})})
    by_fw = [{"fw": fw, "devices": len(devs), "n": sum(t["fw"] == fw for t in tickets),
              "by_category": {k: sum(t["fw"] == fw and t["category"] == k for t in tickets) for k in CATEGORY}}
             for fw, devs in sorted(fw_devices.items())]
    parts = sorted({t["part"] for t in tickets if t["part"]})
    by_batch = [{"batch": b, "devices": len(devs), "n": sum(t["batch"] == b for t in tickets),
                 "parts": {p: sum(t["batch"] == b and t["part"] == p for t in tickets) for p in parts}}
                for b, devs in sorted(batch_devices.items())]
    counts: dict[str, list[dict[str, Any]]] = {}
    for t in tickets:
        counts.setdefault(t["device"], []).append(t)
    repeat = [{"device": d, "site": ts[0]["site"], "n": len(ts), "categories": [CATEGORY[t["category"]] for t in ts]}
              for d, ts in sorted(counts.items()) if len(ts) >= REPEAT_AT_LEAST]

    findings = []
    # 韌體：某一版的某類問題每台件數超過其他版本的 2 倍、而且至少 3 件（剛好 2 倍不列：容易被硬體問題混進來）
    for key, label in CATEGORY.items():
        for row in by_fw:
            n, others = row["by_category"][key], [r for r in by_fw if r is not row]
            other_n = sum(r["by_category"][key] for r in others)
            other_dev = sum(r["devices"] for r in others)
            if n >= FINDING_AT_LEAST and other_dev and n / row["devices"] > FINDING_RATIO * max(other_n / other_dev, 1e-9):
                findings.append(UI_TEXT["f_fw"].format(fw=row["fw"], cat=label, n=n, r=_per_device(n, row["devices"]),
                                                      o=_per_device(other_n, other_dev)))
    # 硬體：某批次更換某零件的次數，至少 3 次、而且其他批次合計不到它的一半
    for part in parts:
        for row in by_batch:
            n = row["parts"][part]
            other = sum(r["parts"][part] for r in by_batch if r is not row)
            if n >= FINDING_AT_LEAST and other * 2 < n:
                findings.append(UI_TEXT["f_batch"].format(batch=row["batch"], part=part, n=n, d=row["devices"], o=other))
    # 同一類問題都用同一種方式處理：可以考慮在韌體裡自動處理或自我檢查
    for row in by_category:
        done = [t for t in tickets if t["category"] == row["key"] and t["resolved"]]
        if len(done) >= SAME_ACTION_AT_LEAST and len(row["actions"]) == 1:
            findings.append(UI_TEXT["f_action"].format(cat=row["label"], n=len(done), action=row["actions"][0]))

    done_all = [t for t in tickets if t["resolved"]]
    period = [min(t["reported"] for t in tickets)[:10], max(t["reported"] for t in tickets)[:10]]
    return {
        "period": period,
        "sites": sorted({f["site"] for f in fleet}),
        "fleet": len(fleet),
        "tickets": tickets,
        "open": sum(not t["resolved"] for t in tickets),
        "median_h": _median([_hours(t["reported"], t["resolved"]) for t in done_all]),
        "downtime_h": round(sum(t["downtime_min"] or 0 for t in done_all) / 60, 1),
        "by_category": by_category,
        "by_fw": by_fw,
        "by_batch": by_batch,
        "parts": parts,
        "repeat": repeat,
        "findings": findings,
        # 比較圖：每類問題各版本的每台件數、各零件各批次的更換次數；頁面照這裡畫，不在前端重算
        "fw_bars": [{"label": label, "rows": [{"fw": r["fw"], "n": r["by_category"][key], "devices": r["devices"],
                                               "r": _per_device(r["by_category"][key], r["devices"])} for r in by_fw]}
                    for key, label in CATEGORY.items()],
        "batch_bars": [{"part": part, "rows": [{"batch": r["batch"], "n": r["parts"][part], "devices": r["devices"]}
                                               for r in by_batch]} for part in parts],
    }


def place_of(device: str, registry: dict[str, dict[str, Any]]) -> str:
    entry = registry.get(device)
    return str(entry["place"]) if entry else UI_TEXT["place_unknown"]


def place_at(device: str, when: str, registry: dict[str, dict[str, Any]]) -> str:
    """某個時間點的地點：取那之前最後一次設定的地點。回報單是過去的事，不能用移機後的地點。"""
    entry = registry.get(device)
    earlier = [h["place"] for h in entry["history"] if h.get("place") and h["at"] <= when] if entry else []
    return str(earlier[-1]) if earlier else UI_TEXT["place_unknown"]


def simulated_devices(morning: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """四台模擬裝置：(裝置代號, 紀錄)。DEMO-SIM-01 就是原型頁的那一個上午；地點查裝置設定紀錄。"""
    second = _heartbeats("radar10/DEMO-SIM-02", "08:00:00", "11:59:55", busy=("09:00:00", "09:04:00"))
    third = _heartbeats("radar10/DEMO-SIM-03", "08:00:00", "11:40:00")
    # broker 要等 keepalive 逾時才發 offline：放在最後一筆之後 35 秒（docs/05 D3）
    third.append({"timestamp": f"{DAY} 11:40:35", "topic": "radar10/DEMO-SIM-03", "resolution": None,
                  "payload": {"event": "offline"}})
    # 時間格式讀不懂的一筆：只計數、不參與計算（device_events._parse）
    third.insert(100, {"timestamp": f"{DAY}T09:00", "topic": "radar10/DEMO-SIM-03", "resolution": None,
                       "payload": {"event": "absence"}})
    # 10:30 之後就沒再送資料，也沒有 offline：只看自己的紀錄會以為一直在線
    fourth = _heartbeats("radar10/DEMO-SIM-04", "08:00:00", "10:30:00")
    return [("DEMO-SIM-01", morning), ("DEMO-SIM-02", second), ("DEMO-SIM-03", third), ("DEMO-SIM-04", fourth)]


HEARTBEAT = timedelta(seconds=5)   # 實測心跳約每 5 秒一筆（docs/05 A10）
# online 訊息裡韌體版本的欄位名稱還沒確認（docs/05 A28），先找這幾個；找不到就照實寫「沒有找到」
FIRMWARE_KEYS = ("fw", "firmware", "version")


def diagnostics(records: list[dict[str, Any]], summary: Summary) -> dict[str, Any]:
    """遠端判斷問題要的裝置層資訊；不含 payload 的事件內容（有沒有人、跌倒等照護資訊不放進回報單）。

    裝置自己送的紀錄和 broker 發的 offline 分開算：預期筆數只算到裝置最後一筆，「最後一筆」也不含 offline，
    兩者相差幾秒可以拿來核對 keepalive（docs/05 A29、D3）。
    注意：實測資料的 topic 是 radar10/<MAC>。回報對象（例如雷達廠商）算不算第三方（會議清單 F10）、
    要不要把 MAC 換成代號，正式化前要決定；一戶一台時地點名稱也可能認得出是誰。現在只用模擬資料。
    """
    readable = [(datetime.strptime(r["timestamp"], TIMESTAMP_FORMAT), r) for r in records
                if isinstance(r.get("timestamp"), str) and _readable(r["timestamp"])]
    readable.sort(key=lambda tr: tr[0])

    def event(r: dict[str, Any]) -> Any:
        payload = r.get("payload")
        return payload.get("event") if isinstance(payload, dict) else None

    # 規格外的 event 算在「無法判讀」裡，不重複算進裝置紀錄
    unknown = {id(r) for r in summary.unknown_events}
    device = [t for t, r in readable if event(r) != "offline" and id(r) not in unknown]
    offline_msgs = [t for t, r in readable if event(r) == "offline"]
    online_msgs = [(t, r) for t, r in readable if event(r) == "online"]
    pairs = list(pairwise(device))
    gaps = sorted(b - a for a, b in pairs)
    median = gaps[len(gaps) // 2] if gaps else None
    biggest = max(pairs, key=lambda ab: ab[1] - ab[0], default=None)
    # 和一般心跳差不多的最大間隔沒有意義，只列明顯比中位數長的
    if biggest is None or median is None or biggest[1] - biggest[0] <= 2 * median:
        biggest = None
    # 預期筆數：每段在線時段從開始算到這段裡裝置最後一筆（不含 broker 的 offline），以每 5 秒一筆估算
    expected = 0
    for period in summary.online:
        inside = [t for t in device if period.start <= t <= period.end]
        if inside:
            expected += int((inside[-1] - inside[0]) / HEARTBEAT) + 1
    last_offline = offline_msgs[-1] if offline_msgs else None
    before = [t for t in device if last_offline is not None and t <= last_offline]
    firmware = None
    if online_msgs:
        payload = online_msgs[-1][1].get("payload", {})
        firmware = next((str(payload[k]) for k in FIRMWARE_KEYS if k in payload), None)
    sample = (summary.unreadable + summary.unknown_events)[:1]
    return {
        "topic": next((str(r["topic"]) for r in records if r.get("topic")), ""),
        "device_records": len(device),
        "offline_records": len(offline_msgs),
        "all_records": len(records),
        "expected_records": expected,
        "median_gap_s": int(median.total_seconds()) if median else None,
        "max_gap": [_stamp(biggest[0]), _stamp(biggest[1]), int((biggest[1] - biggest[0]).total_seconds())] if biggest else None,
        "last_device": _stamp(device[-1]) if device else None,
        "offline_msg": _stamp(last_offline) if last_offline else None,
        "offline_after_s": int((last_offline - before[-1]).total_seconds()) if last_offline and before else None,
        "last_online_msg": _stamp(online_msgs[-1][0]) if online_msgs else None,
        "firmware": firmware,
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


def device_rows(devices: list[tuple[str, list[dict[str, Any]]]],
                registry: dict[str, dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """系統管理人員看的裝置清單：只有裝置狀態，不含照護事件。

    基準時間是所有裝置裡最晚收到資料的時間（模擬用；正式版要用伺服器目前時間，否則全部裝置都停送時基準也跟著停）：最後收到資料比基準落後
    超過心跳間隔、又沒有送 offline 的裝置，判成「沒收到資料」，不能因為自己的紀錄裡沒斷線就當成在線。
    """
    summaries = [(name, place_of(name, registry), records, summarize(records)) for name, records in devices]
    for name, _, _, summary in summaries:
        if not summary.online:
            raise ValueError(f"{name} 沒有讀得懂的紀錄")
    ref = max(summary.online[-1].end for *_, summary in summaries)
    rows = []
    for name, place, records, summary in summaries:
        last = summary.online[-1].end
        diag = diagnostics(records, summary)
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
            # 最後收到「裝置」的資料：不含 broker 發的斷線訊息（回報單也這樣寫，兩處一致）
            "last_seen": diag["last_device"] or _stamp(last),
            "since": _stamp(since) if since else None,
            "minutes_without_data": int((ref - since).total_seconds() // 60) if since else None,
            "from": _stamp(summary.online[0].start),
            "online": [[_stamp(p.start), _stamp(p.end)] for p in summary.online],
            "offline": [[_stamp(a.end), _stamp(b.start)] for a, b in zip(summary.online, summary.online[1:], strict=False)],
            "records": len(records),
            "unreadable": len(summary.unreadable) + len(summary.unknown_events),
            "errors": len(summary.errors),
            "diag": diag,
            # 設定紀錄：沒有就是 None，畫面上算異常（不知道裝置裝在哪裡）
            "registry": registry.get(name),
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
    "family_head": "這段時間有 {n} 件通知：人員已處置 {done} 件、處理中 {doing} 件、尚未確認 {pending} 件。",
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
    "place_unknown": "未登記地點",
    "admin_unregistered": "沒有裝置設定紀錄，不知道裝在哪裡",
    "r_place": "安裝地點：{place}（{area}，{use}）；設定紀錄最後更新 {at}，{by}：{change}",
    "r_place_unknown": "安裝地點：沒有裝置設定紀錄",
    "report_title": "回報給技術支援窗口（待確認）",
    "report_intro": "下面是系統自動整理的裝置資訊，不含照護事件與長者資料；補上描述就能送出。",
    "r_head": "【裝置問題回報（模擬）】{device}（{place}）",
    "r_status": "狀態：{status}。判斷依據：{basis}",
    "r_status_plain": "狀態：{status}",
    "r_unregistered": "異常：沒有裝置設定紀錄，不知道裝在哪裡",
    "r_basis_offline": "{time} 收到斷線訊息（由 broker 發出），之後沒有再收到資料",
    "r_basis_silent": "最後收到資料 {time}，之後超過斷線判斷門檻（{gap} 秒，對應 keepalive）沒有資料，也沒有收到斷線訊息",
    "r_basis_unreadable": "裝置在線，但有 {n} 筆無法判讀的紀錄",
    "r_basis_errors": "裝置在線，但回報錯誤 {n} 次",
    "r_base": "基準時間：{ref}；距最後收到資料 {min} 分鐘",
    "r_base_offline": "基準時間：{ref}；距收到斷線訊息 {min} 分鐘",
    "r_topic": "裝置主題：{topic}",
    "r_online": "這段時間的在線時段：{spans}",
    "r_records": "紀錄：收到可判讀的裝置紀錄 {n} 筆（另有無法判讀 {u} 筆、斷線訊息 {o} 筆{other}，共 {t} 筆）；各在線時段從第一筆到裝置最後一筆，以每 {s} 秒一筆估算約 {e} 筆",
    "r_other": "、重複或伺服器指令 {n} 筆",
    "r_median": "紀錄間隔中位數 {m} 秒",
    "r_maxgap": "明顯較長的紀錄間隔：{g} 秒（{a}–{b}）",
    "r_last": "裝置最後一筆紀錄：{time}",
    "r_offline_msg": "收到斷線訊息：{time}（距裝置最後一筆 {s} 秒，可核對 keepalive）",
    "r_last_online": "最近一次上線訊息：{time}",
    "r_firmware": "韌體版本：{v}",
    "r_firmware_none": "韌體版本：上線訊息裡沒有找到（已找 {keys}；欄位名稱待 A28 確認）",
    "r_source": "資料來源：模擬資料（雲端保存的裝置事件格式）；時間是接收端收到時補上的，不是裝置時間",
    "r_unreadable": "無法判讀的紀錄 {n} 筆，第一筆的形狀（payload 只留欄位名稱）：{sample}",
    "r_errors": "裝置錯誤 {n} 次：{list}",
    "r_peers": "同時段其他裝置：{list}",
    "r_urgency": "急迫程度：{u}",
    "r_checked": "現場已檢查：{list}",
    "r_desc": "管理員描述：{text}",
    "urgency_care": "影響照護，請盡快處理",
    "urgency_later": "可排程處理",
    "checked_none": "未勾選",
    "label_desc": "問題描述（不寫長者或人員姓名）",
    "need_urgency": "請選擇急迫程度。",
    "need_desc": "請填問題描述。",
    "report_sent": "已送出（示意）：回報單 {id}，等待技術支援回覆。這一頁不會真的寄出。",
    # 維修紀錄（系統管理人員看自己場域）與維運後台（2026-10-22 使用者：維修紀錄自動回報、系統後台整理維護經驗幫助韌體與硬體更新）
    "t_sent": "已送出 {time}",
    "t_accepted": "已受理 {time}",
    "t_doing": "處理中",
    "t_done": "{outcome} {time}",
    "t_result": "處理方式：{action}",
    "t_part": "更換零件：{part}",
    "t_fw": "韌體：{a} → {b}",
    "t_downtime": "停機約 {h} 小時",
    "t_handler": "處理人：{who}",
    "t_waiting": "等待技術支援受理（示意）",
    "t_place": "回報當時地點：{place}",
    "own_tickets_note": "只顯示本場域（{site}）的回報單；處理結果由技術支援結案時自動回到這裡（模擬資料）。",
    "ops_note": "跨場域的維修統計，只用場域代號與裝置代號，不含地點名稱、MAC、照護事件與長者資料（模擬資料）。",
    "ops_period": "{a}–{b}，{s} 個場域、{d} 台裝置",
    "ops_total": "回報單 {n} 件",
    "ops_open": "處理中 {n} 件",
    "ops_median": "從送出到結案的時間中位數 {h} 小時",
    "ops_downtime": "已結案的停機合計約 {h} 小時",
    "ops_none": "目前沒有符合規則的項目。",
    "bar_fw": "「{cat}」韌體 {fw}：每台 {r} 件（{n} 件，用過這版的裝置 {d} 台）",
    "bar_batch": "{part}批次 {batch}：更換 {n} 次（這批共 {d} 台）",
    "ops_rule": "列入規則：韌體每台件數超過其他版本的 {r} 倍且至少 {n} 件；硬體批次更換同一零件至少 {n} 次且其他批次合計不到一半；同一類問題至少 {a} 件已結案、都用同一種方式處理。這是值得檢查的項目，不是原因判斷。",
    "ops_repeat_rule": "同一台回報 {k} 次以上就列在這裡，建議追蹤。",
    "ops_repeat_none": "沒有回報 {k} 次以上的裝置。",
    "f_fw": "韌體 {fw} 的「{cat}」回報 {n} 件（每台 {r} 件），其他版本每台 {o} 件；建議列入韌體檢查。",
    "f_batch": "硬體批次 {batch} 更換{part} {n} 次（這批共 {d} 台），其他批次合計 {o} 次；建議檢查這批的{part}。",
    "f_action": "「{cat}」{n} 件都以「{action}」處理；可以考慮讓韌體自己檢查這一項。",
}
# 記錄處置的結果選項：只描述做了什麼，不判斷長者狀況（判斷寫在備註，由人負責）
RESULTS = ["已到場確認", "已到場協助", "以電話或語音確認", "其他（見備註）"]
# 家屬看不到備註：「其他（見備註）」在家屬畫面改寫成「其他處置」（#74 審查 N2）
FAMILY_RESULTS = {"其他（見備註）": "其他處置"}


def check_ui_text(registry: dict[str, dict[str, Any]] | None = None,
                  tickets: list[dict[str, Any]] | None = None) -> None:
    texts = [*UI_TEXT.items(), *((f"result{i}", r) for i, r in enumerate(RESULTS)), *FAMILY_RESULTS.items(),
             *CAUSE.items(), *CATEGORY.items(), ("trend_rule", trend_rule())]
    for device, entry in (registry or {}).items():
        texts += [(f"{device}.{k}", str(entry[k])) for k in ("place", "area", "use")]
        texts += [(f"{device}.history", h["change"]) for h in entry["history"]]
        texts += [(f"{device}.history.place", h["place"]) for h in entry["history"] if h.get("place")]
    # 回報單的處理結果會直接顯示在頁面上
    for t in tickets or []:
        texts += [(f"{t['id']}.{k}", str(t[k])) for k in ("outcome", "action", "part") if t[k]]
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
    registry = load_registry()
    # 居服員畫面的句子會進照護紀錄：裝置沒有設定紀錄就不產生（不能出現「未登記地點雷達偵測到…」）
    if CARE_DEVICE not in registry:
        raise ValueError(f"{CARE_DEVICE} 沒有裝置設定紀錄，居服員畫面寫不出地點")
    caregiver = build.demo_data(place=place_of(CARE_DEVICE, registry))
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
    ref, devices = device_rows(simulated_devices(morning), registry)
    fleet, tickets = load_maintenance()
    ops = ops_data(fleet, tickets)
    check_ui_text(registry, tickets)
    problems = [f for f in ops["findings"] if wording_problems(f)]
    if problems:
        raise ValueError(f"維運後台的參考清單沒通過用語檢查：{problems}")
    return {"caregiver": caregiver, "family": family, "devices": devices, "devices_ref": ref,
            "gap_s": int(HEARTBEAT_GAP.total_seconds()),
            "heartbeat_s": int(HEARTBEAT.total_seconds()), "firmware_keys": "、".join(FIRMWARE_KEYS),
            "own_site": OWN_SITE, "own_tickets": [{**t, "place": place_at(t["device"], t["reported"], registry)}
                            for t in tickets if t["site"] == OWN_SITE], "ops": ops,
            "categories": CATEGORY, "ops_rule": {"r": FINDING_RATIO, "n": FINDING_AT_LEAST, "a": SAME_ACTION_AT_LEAST, "k": REPEAT_AT_LEAST},
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
