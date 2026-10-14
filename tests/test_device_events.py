"""整理裝置事件（docs/adr/0001）：只透過 device_events.summarize() 驗證。

測試資料全部手寫，格式照雲端存的裝置事件外殼：
{"timestamp", "topic", "resolution", "payload"}（見 docs/05 A2、A10）。
"""
from datetime import datetime, timedelta

import pytest

from radar_agent.device_events import summarize

TOPIC = "radar10/DEVICE01"


def t(hms: str) -> datetime:
    return datetime.strptime(f"2026-10-13 {hms}", "%Y-%m-%d %H:%M:%S")


def rec(hms: str, event: str, **extra) -> dict:
    payload = {"mode": "radar", "event": event, "trigger": "track", "userReply": False}
    payload.update(extra)
    return {"timestamp": f"2026-10-13 {hms}", "topic": TOPIC, "resolution": None, "payload": payload}


def test_one_visit_runs_from_presence_to_absence():
    summary = summarize([
        rec("10:00:00", "absence"),
        rec("10:00:05", "presence"),
        rec("10:00:10", "moving"),
        rec("10:00:15", "static"),
        rec("10:00:20", "absence"),
    ])
    assert len(summary.visits) == 1
    visit = summary.visits[0]
    assert (visit.start, visit.end) == (t("10:00:05"), t("10:00:20"))
    assert visit.duration == timedelta(seconds=15)


def test_exact_duplicates_are_counted_once():
    """同一秒、內容完全一樣的紀錄是重送（docs/05 A11），只算一次並記下刪了幾筆。"""
    summary = summarize([
        rec("10:00:05", "presence"),
        rec("10:00:15", "static"),
        rec("10:00:15", "static"),
        rec("10:00:20", "absence"),
        rec("10:00:20", "absence"),
    ])
    assert summary.duplicates_removed == 2
    assert [(v.start, v.end) for v in summary.visits] == [(t("10:00:05"), t("10:00:20"))]


def test_heartbeat_gap_longer_than_30_seconds_splits_online_periods():
    """沒人時也約每 5 秒一筆；超過 30 秒沒有紀錄就算斷線（暫定門檻，docs/05 D3）。"""
    summary = summarize([
        rec("10:00:00", "absence"),
        rec("10:00:05", "absence"),
        rec("10:00:10", "absence"),
        rec("10:00:41", "absence"),
        rec("10:00:46", "absence"),
    ])
    assert [(p.start, p.end) for p in summary.online] == [
        (t("10:00:00"), t("10:00:10")),
        (t("10:00:41"), t("10:00:46")),
    ]


def test_gap_of_exactly_30_seconds_is_still_online():
    summary = summarize([rec("10:00:00", "absence"), rec("10:00:30", "absence")])
    assert [(p.start, p.end) for p in summary.online] == [(t("10:00:00"), t("10:00:30"))]


def test_offline_message_ends_online_period_even_within_30_seconds():
    """offline 由 broker 在太久沒收到訊息時發出（docs/05 A29），收到就算斷線。"""
    offline = {"timestamp": "2026-10-13 10:00:08", "topic": TOPIC, "resolution": None,
               "payload": {"event": "offline"}}
    summary = summarize([
        rec("10:00:00", "absence"),
        rec("10:00:05", "absence"),
        offline,
        rec("10:00:20", "absence"),
    ])
    assert [(p.start, p.end) for p in summary.online] == [
        (t("10:00:00"), t("10:00:08")),
        (t("10:00:20"), t("10:00:20")),
    ]


def test_visit_cut_by_disconnection_is_not_reported_as_a_long_stay():
    """斷線期間不知道人何時離開；這次造訪另外列出，不算進停留時間。"""
    summary = summarize([
        rec("10:00:00", "presence"),
        rec("10:00:05", "moving"),
        rec("10:05:00", "absence"),
    ])
    assert summary.visits == []
    assert [(p.start, p.end) for p in summary.interrupted_visits] == [(t("10:00:00"), t("10:00:05"))]


def fall(hms: str, trigger: str = "timeout", mode: str = "radar", user_reply: bool = False) -> dict:
    return rec(hms, "fall", trigger=trigger, mode=mode, userReply=user_reply, confidence=100)


def test_counts_fall_alerts_and_no_need_help_replies():
    """C2：跌倒以裝置事件的 fall 為準；回答「沒事」是 resolved + noNeedHelp（docs/05 A30），分開列。"""
    summary = summarize([
        rec("10:00:00", "presence"),
        fall("10:00:10"),
        rec("10:00:30", "resolved", trigger="noNeedHelp", mode="speech", userReply=True, confidence=100),
        rec("10:00:40", "absence"),
        rec("10:01:00", "presence"),
        fall("10:01:10", trigger="conversation", mode="fusion", user_reply=True),
        rec("10:01:20", "absence"),
    ])
    assert [a.time for a in summary.fall_alerts] == [t("10:00:10"), t("10:01:10")]
    assert summary.no_need_help_replies == [t("10:00:30")]


def test_repeated_fall_messages_before_resolution_are_one_alert():
    """fallRepeat：跌倒後一直沒起身，fall 會持續送出（docs/05 A26），同一次警報只算一次。"""
    summary = summarize([
        rec("10:00:00", "presence"),
        fall("10:00:10"),
        fall("10:00:20"),
        fall("10:00:30"),
        rec("10:00:40", "resolved", trigger="standUp", mode="radar", confidence=100),
        fall("10:00:50"),
    ])
    assert [a.time for a in summary.fall_alerts] == [t("10:00:10"), t("10:00:50")]


def test_speech_fall_is_marked_as_distress_call():
    """event=fall 且 mode=speech 是主動呼救（DistressCall），不一定跌倒，要能分開看。"""
    summary = summarize([fall("10:00:10", mode="speech")])
    assert [a.distress_call for a in summary.fall_alerts] == [True]


CONFIG = {"fallSwitch": False, "fallSensitiveLevel": 3, "fallRepeat": False, "stillThreshold": 30}


def test_device_config_report_is_recorded_as_a_setting_change():
    """10/12 實測：下設定（.../config/set）後，裝置在自己的 topic 回報 params.sys.config。
    以裝置回報為準；下設定的指令不是裝置送的，也不算心跳。"""
    command = {"timestamp": "2026-10-13 10:00:30", "topic": TOPIC + "/config/set",
               "resolution": None, "payload": dict(CONFIG)}
    report = {"timestamp": "2026-10-13 10:00:30", "topic": TOPIC, "resolution": None,
              "payload": {"params": {"sys.config": dict(CONFIG)}}}
    summary = summarize([rec("10:00:00", "absence"), command, report])
    assert [(c.time, c.values) for c in summary.setting_changes] == [(t("10:00:30"), CONFIG)]
    assert [(p.start, p.end) for p in summary.online] == [(t("10:00:00"), t("10:00:30"))]


def test_config_command_alone_is_not_a_heartbeat():
    command = {"timestamp": "2026-10-13 10:00:40", "topic": TOPIC + "/config/set",
               "resolution": None, "payload": dict(CONFIG)}
    summary = summarize([rec("10:00:00", "absence"), command])
    assert summary.setting_changes == []
    assert [(p.start, p.end) for p in summary.online] == [(t("10:00:00"), t("10:00:00"))]


def test_unreadable_records_are_kept_as_is_and_skipped():
    """時間或格式讀不懂的紀錄原樣列出，不丟掉也不猜（同週報原則 5，docs/07）。"""
    bad_time = {"timestamp": "13/10/2026", "topic": TOPIC, "resolution": None,
                "payload": {"event": "absence"}}
    no_payload = {"timestamp": "2026-10-13 10:00:05", "topic": TOPIC, "resolution": None}
    summary = summarize([rec("10:00:00", "absence"), bad_time, no_payload])
    assert summary.unreadable == [bad_time, no_payload]
    assert [(p.start, p.end) for p in summary.online] == [(t("10:00:00"), t("10:00:00"))]


def test_unknown_event_counts_as_heartbeat_but_is_listed():
    """裝置送了沒列在規格裡的 event：裝置還在線，但要列出來請人確認（docs/05 A3）。"""
    odd = rec("10:00:05", "jumping")
    summary = summarize([rec("10:00:00", "absence"), odd])
    assert summary.unknown_events == [odd]
    assert [(p.start, p.end) for p in summary.online] == [(t("10:00:00"), t("10:00:05"))]


def test_error_messages_are_listed():
    """error 格式：{"event":"error","message":"radar is not running"}（docs/05 A29）。"""
    error = {"timestamp": "2026-10-13 10:00:05", "topic": TOPIC, "resolution": None,
             "payload": {"event": "error", "message": "radar is not running"}}
    summary = summarize([error])
    assert summary.errors == [(t("10:00:05"), "radar is not running")]


def test_records_out_of_order_give_the_same_result():
    """重送的紀錄可能晚到（docs/05 A11），整理前先依時間排序。"""
    in_order = [
        rec("10:00:00", "absence"),
        rec("10:00:05", "presence"),
        rec("10:00:10", "moving"),
        rec("10:00:20", "absence"),
    ]
    shuffled = [in_order[2], in_order[0], in_order[3], in_order[1]]
    assert summarize(shuffled) == summarize(in_order)


def test_offline_during_visit_interrupts_it():
    offline = {"timestamp": "2026-10-13 10:00:20", "topic": TOPIC, "resolution": None,
               "payload": {"event": "offline"}}
    summary = summarize([rec("10:00:00", "presence"), rec("10:00:05", "moving"), offline])
    assert summary.visits == []
    assert [(p.start, p.end) for p in summary.interrupted_visits] == [(t("10:00:00"), t("10:00:05"))]


def test_visit_still_open_at_end_of_data_is_reported_as_ongoing():
    """資料結束時人還在：不能丟掉（可能就是久待未出），也還不能算成完整的造訪。"""
    summary = summarize([rec("10:00:00", "presence"), rec("10:00:05", "static")])
    assert summary.visits == []
    assert summary.ongoing_visit is not None
    assert (summary.ongoing_visit.start, summary.ongoing_visit.end) == (t("10:00:00"), t("10:00:05"))


def test_longstay_alerts_are_listed():
    """久待未出是 Demo 主打；裝置滯留超過 stillThreshold 送出 longstay（docs/05 A26）。"""
    summary = summarize([
        rec("10:00:00", "presence"),
        rec("10:30:00", "longstay", trigger="timeout", delaysec=600, confidence=100),
    ])
    assert summary.longstay_alerts == [t("10:30:00")]


def test_records_from_two_devices_are_rejected():
    """一次只整理一台裝置；混在一起會把兩個地點的造訪切錯。"""
    other = rec("10:00:05", "absence")
    other["topic"] = "radar10/DEVICE02"
    with pytest.raises(ValueError, match="DEVICE02"):
        summarize([rec("10:00:00", "absence"), other])


def test_record_without_topic_is_unreadable_not_another_device():
    no_topic = {"timestamp": "2026-10-13 10:00:05", "payload": {"event": "absence"}}
    summary = summarize([rec("10:00:00", "absence"), no_topic])
    assert summary.unreadable == [no_topic]
