"""居服員 Profile 的範本與輸出檢查（docs/02「依 Profile 改寫」範本層、輸出檢查）。

只透過 caregiver_text()、check_output() 與命令列 main() 驗證；期望的句子是在對話中確認過的寫法（2026-10-14）。
"""
import json
from pathlib import Path

from radar_agent.caregiver import caregiver_report, caregiver_text, event_notes
from radar_agent.device_events import summarize
from radar_agent.output_check import check_output

TOPIC = "radar10/DEVICE01"


def rec(hms: str, event: str, day: str = "2026-10-14", **extra) -> dict:
    payload = {"mode": "radar", "event": event, "trigger": "track", "userReply": False}
    payload.update(extra)
    return {"timestamp": f"{day} {hms}", "topic": TOPIC, "resolution": None, "payload": payload}


def _sec(hms: str) -> int:
    h, m, s = map(int, hms.split(":"))
    return h * 3600 + m * 60 + s


def _hms(sec: int) -> str:
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def timeline(start: str, end: str, visits: list[tuple[str, str]] = (), step: int = 5) -> list[dict]:
    """模擬裝置：start 到 end 每 step 秒一筆；造訪開始送 presence、期間送 moving、結束送 absence，其餘送 absence。"""
    spans = [(_sec(a), _sec(b)) for a, b in visits]
    out = []
    for now in range(_sec(start), _sec(end) + 1, step):
        event = "absence"
        for a, b in spans:
            if now == a:
                event = "presence"
            elif a < now < b:
                event = "moving"
        out.append(rec(_hms(now), event))
    return out


def test_site_summary_all_online():
    summary = summarize(timeline("09:00:00", "09:30:00", [("09:05:00", "09:13:00"), ("09:20:00", "09:21:30")]))
    assert caregiver_text(summary, place="1 號廁所") == (
        "1 號廁所 10/14：有人使用 2 次，最長一次 8 分鐘。\n沒有跌倒警報、主動呼救或久待警報。"
    )


def test_disconnection_is_stated_and_zero_falls_is_limited_to_online_time():
    """斷線期間不能寫成 0 次跌倒（REVIEW.md 3.1 沒有資料不等於 0）。"""
    records = timeline("09:00:00", "09:10:00", [("09:02:00", "09:03:00")]) + timeline("09:40:00", "09:50:00")
    assert caregiver_text(summarize(records), place="1 號廁所") == (
        "1 號廁所 10/14：有人使用 1 次，停留 1 分鐘。\n在線期間沒有跌倒警報、主動呼救或久待警報。\n"
        "09:10–09:40 裝置斷線，這段時間無法判斷是否有跌倒。"
    )


def test_no_data_at_all_says_cannot_judge():
    assert caregiver_text(summarize([]), place="1 號廁所") == "1 號廁所：沒有收到資料，無法判斷。"


def test_online_without_visits_says_no_use_detected():
    assert caregiver_text(summarize(timeline("09:00:00", "09:10:00")), place="1 號廁所") == (
        "1 號廁所 10/14：沒有偵測到有人使用。\n沒有跌倒警報、主動呼救或久待警報。"
    )


def test_alerts_are_followed_by_their_outcome_and_calls_are_counted_apart():
    """C2：主動呼救（DistressCall）不算進跌倒（2026-10-16 第四次書面回覆）；
    每個警報後面接它的解除（2026-10-29 確認，A＋B）；沒有解除紀錄就照實寫，不說成仍未處理。"""
    records = timeline("09:00:00", "09:50:00", [("09:04:00", "09:08:00"), ("09:19:00", "09:46:00")])
    records += [
        rec("09:05:00", "fall", trigger="timeout", userReply=False, confidence=100),
        rec("09:05:20", "resolved", trigger="noNeedHelp", mode="speech", userReply=True, confidence=100),
        rec("09:20:00", "fall", trigger="timeout", mode="speech", userReply=False, confidence=100),
        rec("09:45:00", "longstay", trigger="timeout", delaysec=600, confidence=100),
    ]
    assert caregiver_text(summarize(records), place="1 號廁所") == (
        "1 號廁所 10/14：有人使用 2 次，最長一次 27 分鐘。\n"
        "跌倒警報 1 次：09:05 語音詢問未獲回應，20 秒後長者回覆沒事，警報解除。\n"
        "主動呼救 1 次：09:20，資料中沒有解除紀錄。\n"
        "久待警報 1 次（09:45）。"
    )


def test_interrupted_and_ongoing_visits_are_not_dropped():
    records = (timeline("09:00:00", "09:10:00", [("09:08:00", "09:30:00")])
               + timeline("09:40:00", "09:50:00", [("09:45:00", "10:30:00")]))
    assert caregiver_text(summarize(records), place="1 號廁所") == (
        "1 號廁所 10/14：沒有偵測到有人使用。\n另有 1 次使用途中裝置斷線，停留時間不明。\n"
        "資料結束時仍有人在內（09:45 起）。\n"
        "在線期間沒有跌倒警報、主動呼救或久待警報。\n"
        "09:10–09:40 裝置斷線，這段時間無法判斷是否有跌倒。"
    )


def test_errors_and_unreadable_records_ask_for_it_staff():
    records = [
        *timeline("09:00:00", "09:10:00"),
        {"timestamp": "2026-10-14 09:05:00", "topic": TOPIC, "resolution": None,
         "payload": {"event": "error", "message": "radar is not running"}},
        {"timestamp": "壞掉的時間", "topic": TOPIC, "resolution": None, "payload": {"event": "absence"}},
    ]
    text = caregiver_text(summarize(records), place="1 號廁所")
    assert text.endswith("裝置回報錯誤 1 次（09:05），請通知資訊人員。\n另有 1 筆紀錄無法判讀，請通知資訊人員。")


def test_personal_assessment_quotes_kg_text_verbatim():
    """知識圖譜的等級與建議原文照搬，只補前後句子；未核准要標明僅供測試。"""
    from radar_agent import kg_rules
    assessment = kg_rules.assess(kg_rules.load(), "來回行走", "5.4")
    assert caregiver_text(assessment) == (
        "來回行走 5.4 秒（2.44 公尺），屬於「很好」（4 分）。\n"
        "建議：跌倒風險極低，持續規律步行，並搭配肌力與平衡訓練維持步態穩定。\n"
        "建議量：每週 165-195 分鐘；每次快走 25-30 分鐘；步速 ≥ 1.0 m/s\n"
        "（知識圖譜尚未核准，僅供測試）"
    )


def test_personal_assessment_that_cannot_be_judged_gives_no_level():
    from radar_agent import kg_rules
    kg = kg_rules.load()
    assert caregiver_text(kg_rules.assess(kg, "來回行走", "5.45")) == (
        "來回行走 5.45 秒（2.44 公尺）：目前無法評估，已交由審核人確認。\n（知識圖譜尚未核准，僅供測試）"
    )
    assert caregiver_text(kg_rules.assess(kg, "握力", "30")) == (
        "握力：目前沒有評估規則，已送審核。\n（知識圖譜尚未核准，僅供測試）"
    )


ONLINE_TWO_VISITS = [("09:05:00", "09:13:00"), ("09:20:00", "09:21:30")]


def test_check_flags_a_number_that_is_not_in_the_facts():
    """改寫（日後的在地模型）不能新增或改動數字（原則 2）。"""
    summary = summarize(timeline("09:00:00", "09:30:00", ONLINE_TWO_VISITS))
    text = caregiver_text(summary, place="1 號廁所").replace("有人使用 2 次", "有人使用 3 次")
    assert check_output(text, summary, place="1 號廁所") == ["數字 3 不在事實裡"]


def test_check_flags_zero_distress_calls_when_device_was_offline():
    records = timeline("09:00:00", "09:10:00") + timeline("09:40:00", "09:50:00")
    summary = summarize(records)
    text = caregiver_text(summary, place="1 號廁所") + "\n主動呼救 0 次。"
    assert check_output(text, summary, place="1 號廁所") == ["裝置有斷線，不能寫「主動呼救 0 次」而不註明在線期間"]


def test_check_flags_medical_wording_and_false_alarm():
    summary = summarize(timeline("09:00:00", "09:30:00", ONLINE_TWO_VISITS))
    text = caregiver_text(summary, place="1 號廁所") + "建議就醫診斷。其中 1 次是誤報。"
    problems = check_output(text, summary, place="1 號廁所")
    assert "不能出現「診斷」（非醫療器材）" in problems
    assert "不能寫「誤報」（C2：語音回覆沒事不直接算誤報）" in problems


def test_check_flags_zero_falls_when_device_was_offline():
    """REVIEW.md 3.1：沒有資料不等於 0。"""
    summary = summarize(timeline("09:00:00", "09:10:00") + timeline("09:40:00", "09:50:00"))
    text = caregiver_text(summary, place="1 號廁所").replace("在線期間沒有", "沒有")
    assert check_output(text, summary, place="1 號廁所") == [
        "裝置有斷線，不能寫「沒有跌倒警報／主動呼救／久待警報」而不註明在線期間"
    ]


def _scenarios():
    yield summarize([])
    yield summarize(timeline("09:00:00", "09:10:00"))
    yield summarize(timeline("09:00:00", "09:30:00", ONLINE_TWO_VISITS))
    yield summarize(timeline("09:00:00", "09:10:00", [("09:02:00", "09:03:00")]) + timeline("09:40:00", "09:50:00"))
    yield summarize(timeline("09:00:00", "09:10:00", [("09:08:00", "09:30:00")])
                    + timeline("09:40:00", "09:50:00", [("09:45:00", "10:30:00")]))
    yield summarize([
        *timeline("09:00:00", "09:50:00", [("09:04:00", "09:08:00")]),
        rec("09:05:00", "fall", trigger="timeout", mode="speech", userReply=False, confidence=100),
        rec("09:05:20", "resolved", trigger="noNeedHelp", mode="speech", userReply=True, confidence=100),
        rec("09:45:00", "longstay", trigger="timeout", delaysec=600, confidence=100),
        {"timestamp": "2026-10-14 09:30:00", "topic": TOPIC, "resolution": None,
         "payload": {"event": "error", "message": "radar is not running"}},
    ])


def test_every_site_template_passes_its_own_check():
    for summary in _scenarios():
        text = caregiver_text(summary, place="1 號廁所")
        assert check_output(text, summary, place="1 號廁所") == [], text


def test_every_personal_template_passes_its_own_check():
    from radar_agent import kg_rules
    kg = kg_rules.load()
    for spec in kg.thresholds.values():
        for level in spec["levels"]:
            for value in (level.get("min"), level.get("max")):
                if value is None:
                    continue
                a = kg_rules.assess(kg, spec["name"], str(value))
                assert check_output(caregiver_text(a), a) == [], (spec["name"], value)
    for metric, value in (("來回行走", "5.45"), ("握力", "30")):
        a = kg_rules.assess(kg, metric, value)
        assert check_output(caregiver_text(a), a) == []


def test_medical_word_inside_quoted_kg_text_is_allowed_but_not_when_added():
    """知識圖譜原文（例如「物理治療師」）由審核人把關，照搬可以；範本或改寫另外加上去的不行。"""
    from radar_agent import kg_rules
    a = kg_rules.assess(kg_rules.load(), "下肢肌力", "0")
    text = caregiver_text(a)
    assert "物理治療師" in text and check_output(text, a) == []
    assert check_output(text + "需要治療。", a) == ["不能出現「治療」（非醫療器材）"]


def test_duration_wording_for_seconds_and_minutes_with_seconds():
    once_90s = summarize(timeline("09:00:00", "09:10:00", [("09:01:00", "09:02:30")]))
    assert "有人使用 1 次，停留 1 分 30 秒。" in caregiver_text(once_90s, place="1 號廁所")
    twice = summarize(timeline("09:00:00", "09:10:00", [("09:01:00", "09:01:45"), ("09:03:00", "09:03:30")]))
    assert "有人使用 2 次，最長一次 45 秒。" in caregiver_text(twice, place="1 號廁所")


def test_data_spanning_two_days_shows_the_date_range():
    records = [rec("23:59:50", "absence", day="2026-10-13"), rec("23:59:55", "absence", day="2026-10-13"),
               rec("00:00:00", "absence"), rec("00:00:05", "absence")]
    assert caregiver_text(summarize(records), place="1 號廁所").startswith("1 號廁所 10/13–10/14：")


# ── 命令列（radar-caregiver）────────────────────────────

def _run(capsys, *argv) -> tuple[int, str, str]:
    from radar_agent.caregiver import main

    code = main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return code, out, err


VISITS = [("09:05:00", "09:13:00"), ("09:20:00", "09:21:30")]
# 沒有任何事件：只有摘要，不另寫「沒有事件」
EXPECTED = ("【重點】這段時間沒有警報，裝置都在線。\n\n【事件摘要】\n"
            "1 號廁所 10/14：有人使用 2 次，最長一次 8 分鐘。\n沒有跌倒警報、主動呼救或久待警報。\n")


def test_cli_reads_a_json_array_and_prints_the_checked_text(tmp_path, capsys):
    path = tmp_path / "events.json"
    path.write_text(json.dumps(timeline("09:00:00", "09:30:00", VISITS)), encoding="utf-8")
    assert _run(capsys, path, "--place", "1 號廁所") == (0, EXPECTED, "")


def test_cli_reads_json_lines_with_blank_lines(tmp_path, capsys):
    path = tmp_path / "events.jsonl"
    lines = [json.dumps(r, ensure_ascii=False) for r in timeline("09:00:00", "09:30:00", VISITS)]
    path.write_text("\n".join(lines[:10]) + "\n\n" + "\n".join(lines[10:]) + "\n", encoding="utf-8")
    assert _run(capsys, path, "--place", "1 號廁所") == (0, EXPECTED, "")


def test_cli_lists_records_with_unreadable_fields_instead_of_failing(tmp_path, capsys):
    path = tmp_path / "events.jsonl"
    records = [*timeline("09:00:00", "09:10:00"), {"timestamp": "昨天", "topic": TOPIC, "payload": {}}]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records), encoding="utf-8")
    code, out, _ = _run(capsys, path, "--place", "1 號廁所")
    assert code == 0
    assert out.endswith("另有 1 筆紀錄無法判讀，請通知資訊人員。\n")


def test_cli_rejects_a_line_that_is_not_json_and_says_where(tmp_path, capsys):
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps(rec("09:00:00", "absence")) + "\n{壞掉\n", encoding="utf-8")
    code, out, err = _run(capsys, path, "--place", "1 號廁所")
    assert (code, out) == (2, "")
    assert "第 2 行不是 JSON" in err


def test_cli_rejects_entries_that_are_not_records(tmp_path, capsys):
    path = tmp_path / "events.json"
    path.write_text(json.dumps([rec("09:00:00", "absence"), "absence"]), encoding="utf-8")
    code, out, err = _run(capsys, path, "--place", "1 號廁所")
    assert (code, out) == (2, "")
    assert "第 2 筆不是" in err


def test_cli_refuses_two_devices_in_one_file(tmp_path, capsys):
    other = {**rec("09:00:05", "absence"), "topic": "radar10/DEVICE02"}
    path = tmp_path / "events.json"
    path.write_text(json.dumps([rec("09:00:00", "absence"), other]), encoding="utf-8")
    code, out, err = _run(capsys, path, "--place", "1 號廁所")
    assert (code, out) == (2, "")
    assert "一次只整理一台裝置" in err


def test_cli_reports_a_missing_file(tmp_path, capsys):
    code, out, err = _run(capsys, tmp_path / "沒有這個檔.json", "--place", "1 號廁所")
    assert (code, out) == (2, "")
    assert "沒有這個檔.json" in err


def test_cli_requires_a_place_name(tmp_path, capsys):
    path = tmp_path / "events.json"
    path.write_text("[]", encoding="utf-8")
    assert _run(capsys, path, "--place", "  ")[0] == 2


def test_cli_prints_nothing_for_the_caregiver_when_the_check_fails(tmp_path, capsys):
    """地點名稱帶進醫療用詞，輸出檢查擋下：只在 stderr 說明，不輸出文字。"""
    path = tmp_path / "events.json"
    path.write_text(json.dumps(timeline("09:00:00", "09:10:00")), encoding="utf-8")
    code, out, err = _run(capsys, path, "--place", "治療室")
    assert (code, out) == (1, "")
    assert "輸出檢查沒通過" in err
    assert "治療" in err


def test_cli_reads_sys_argv_when_called_without_arguments(tmp_path, capsys, monkeypatch):
    """console script 呼叫 main() 時不帶參數，要自己從 sys.argv 取。"""
    import sys

    from radar_agent.caregiver import main

    path = tmp_path / "events.json"
    path.write_text(json.dumps(timeline("09:00:00", "09:30:00", VISITS)), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["radar-caregiver", str(path), "--place", "1 號廁所"])
    assert main() == 0
    assert capsys.readouterr().out == EXPECTED


# ── 逐事件的處置提示與照護紀錄草稿（docs/11 G1，用語 2026-10-16 確認）──────

def _fall(hms: str, mode: str, trigger: str, **extra) -> dict:
    return rec(hms, "fall", mode=mode, trigger=trigger, **extra)


def _resolved(hms: str, trigger: str) -> dict:
    return rec(hms, "resolved", mode="speech" if trigger == "noNeedHelp" else "radar", trigger=trigger)


def _notes(records: list[dict]) -> list[tuple[str, str]]:
    return [(n.prompt, n.record) for n in event_notes(summarize(records), "1 號廁所")]


def test_radar_fall_without_reply_asks_to_go_now_and_states_how_long_it_waited():
    records = [*timeline("09:00:00", "09:10:00"), _fall("09:02:00", "radar", "timeout", delaysec=10)]
    assert _notes(records) == [
        ("請立即到場確認。", "09:02 1 號廁所雷達偵測到疑似跌倒，語音詢問 10 秒未獲回應。"),
    ]


def test_wait_time_is_left_out_when_the_device_did_not_send_it():
    records = [*timeline("09:00:00", "09:10:00"), _fall("09:02:00", "radar", "timeout")]
    assert _notes(records) == [("請立即到場確認。", "09:02 1 號廁所雷達偵測到疑似跌倒，語音詢問未獲回應。")]


def test_fusion_fall_and_distress_call_have_their_own_wording():
    records = [*timeline("09:00:00", "09:30:00"), _fall("09:02:00", "fusion", "conversation", userReply=True), _resolved("09:03:00", "standUp"), _fall("09:20:00", "speech", "timeout", delaysec=10)]
    assert _notes(records) == [
        ("長者回覆需要協助，請立即到場。", "09:02 1 號廁所雷達偵測到疑似跌倒，長者語音回覆需要協助。"),
        ("已偵測到起身，警報已解除。", "09:03 偵測到長者起身，警報解除。"),
        ("長者主動呼救，請立即到場。", "09:20 1 號廁所偵測到主動呼救。"),
    ]


def test_unknown_fall_source_is_not_guessed_and_still_asks_to_go():
    records = [*timeline("09:00:00", "09:10:00"), _fall("09:02:00", "radar", "somethingNew")]
    assert _notes(records) == [("請立即到場確認。", "09:02 1 號廁所跌倒警報。")]


def test_full_report_has_summary_prompts_and_record_draft_in_time_order():
    records = timeline("09:00:00", "09:10:00", [("09:01:00", "09:06:00")]) + timeline("09:40:00", "09:50:00")
    records += [
        _fall("09:02:00", "radar", "timeout", delaysec=10),
        _resolved("09:03:00", "noNeedHelp"),
        rec("09:45:00", "longstay", trigger="timeout", delaysec=600),
    ]
    summary = summarize(records)
    report = caregiver_report(summary, place="1 號廁所")
    assert report == (
        "【重點】跌倒警報 1 次已解除；09:45 有 1 次久待警報；09:10–09:40 裝置斷線，期間狀況不明，請通知資訊人員。\n"
        "\n【事件摘要】\n"
        + caregiver_text(summary, place="1 號廁所") + "\n"
        "\n【處置提示】\n"
        "09:02 請立即到場確認。\n"
        "09:03 長者回覆沒事，警報已解除；可視情況前往關心。\n"
        "09:10–09:40 這段時間無法判斷是否有跌倒，請通知資訊人員。\n"
        "09:45 停留超過設定時間，請前往查看。\n"
        "\n【照護紀錄草稿】\n"
        "09:02 1 號廁所雷達偵測到疑似跌倒，語音詢問 10 秒未獲回應。\n"
        "09:03 長者語音回覆沒事，警報解除。\n"
        "09:10–09:40 裝置斷線，期間狀況不明。\n"
        "09:45 1 號廁所停留超過設定時間（久待警報）。"
    )
    assert check_output(report, summary, place="1 號廁所") == []


def test_report_with_every_event_kind_passes_its_own_check():
    records = timeline("09:00:00", "09:30:00") + timeline("09:50:00", "10:00:00") + [
        _fall("09:02:00", "radar", "timeout", delaysec=10), _resolved("09:02:30", "noNeedHelp"),
        _fall("09:05:00", "fusion", "conversation", userReply=True), _resolved("09:06:00", "standUp"),
        _fall("09:10:00", "speech", "conversation", userReply=True), _resolved("09:11:00", "standUp"),
        _fall("09:15:00", "radar", "somethingNew"),
        rec("09:25:00", "longstay", trigger="timeout", delaysec=600),
    ]
    summary = summarize(records)
    assert check_output(caregiver_report(summary, place="1 號廁所"), summary, place="1 號廁所") == []


def test_cli_prints_prompts_and_record_draft(tmp_path, capsys):
    path = tmp_path / "events.json"
    records = [*timeline("09:00:00", "09:10:00"), _fall("09:02:00", "radar", "timeout", delaysec=10)]
    path.write_text(json.dumps(records), encoding="utf-8")
    code, out, _ = _run(capsys, path, "--place", "1 號廁所")
    assert code == 0
    assert out.endswith(
        "【處置提示】\n09:02 請立即到場確認。\n\n【照護紀錄草稿】\n"
        "09:02 1 號廁所雷達偵測到疑似跌倒，語音詢問 10 秒未獲回應。\n"
    )


# ── 給 Dashboard 的 JSON（radar-caregiver --json）─────────────

DEMO = Path(__file__).resolve().parents[1] / "data" / "samples_demo" / "bathroom_morning.jsonl"


def test_json_carries_the_same_sentences_as_the_text_report(capsys):
    code, out, _ = _run(capsys, DEMO, "--place", "1 號廁所", "--json")
    data = json.loads(out)
    _, text, _ = _run(capsys, DEMO, "--place", "1 號廁所")
    assert code == 0 and data["schema"] == "caregiver-report-v0" and data["text_layer"] == "template"
    assert data["summary"] in text
    assert all(e["record"] in text and e["prompt"] in text for e in data["events"])
    assert {e["level"] for e in data["events"]} == {"urgent", "resolved", "check", "device"}
    assert data["counts"]["fall_alerts"] == 2 and data["counts"]["distress_calls"] == 1
    assert data["counts"]["offline"] == [["2026-10-14 11:10:00", "2026-10-14 11:25:00"]]


def test_json_without_any_data_has_no_period(tmp_path, capsys):
    path = tmp_path / "empty.json"
    path.write_text("[]", encoding="utf-8")
    code, out, _ = _run(capsys, path, "--place", "1 號廁所", "--json")
    data = json.loads(out)
    assert code == 0 and (data["from"], data["to"], data["events"]) == (None, None, [])
    assert data["summary"] == "1 號廁所：沒有收到資料，無法判斷。"


def test_json_is_not_printed_when_the_check_fails(monkeypatch, tmp_path, capsys):
    from radar_agent import caregiver

    monkeypatch.setattr(caregiver, "check_output", lambda *a, **k: ["假的問題"])
    code, out, err = _run(capsys, DEMO, "--place", "1 號廁所", "--json")
    assert (code, out) == (1, "") and "假的問題" in err


def test_every_prompt_has_a_level_and_title():
    """處置提示一改，NOTE_KINDS 也要跟著改；否則 --json 會丟錯。"""
    from radar_agent.caregiver import NOTE_KINDS, report_data

    data = report_data(summarize(_jsonl(DEMO)), place="1 號廁所")
    assert {e["prompt"] for e in data["events"]} <= set(NOTE_KINDS)


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_report_says_when_the_device_went_offline_before_the_data_ended():
    """以前會寫「裝置都在線」再被輸出檢查擋下；現在照實寫出斷線時間（2026-10-19）。"""
    records = [*timeline("09:00:00", "09:00:25"),
               rec("09:00:30", "presence"), rec("09:00:35", "moving"), rec("09:00:40", "offline")]
    summary = summarize(records)
    text = caregiver_report(summary, place="1 號廁所")
    assert "在線期間沒有跌倒警報" in text and "09:00 起裝置斷線，之後無法判斷是否有跌倒。" in text
    assert "【重點】09:00 起裝置斷線，之後狀況不明，請通知資訊人員。" in text
    assert "都在線" not in text and check_output(text, summary, place="1 號廁所") == []


def test_report_says_the_device_went_offline_even_when_nobody_was_inside():
    """最後一筆是 offline、當時沒人：以前寫「裝置都在線」而且檢查放行（2026-10-19 審查）。"""
    summary = summarize([*timeline("09:00:00", "09:00:35"), rec("09:00:40", "offline")])
    text = caregiver_report(summary, place="1 號廁所")
    assert "在線期間沒有跌倒警報" in text and "09:00 起裝置斷線，之後無法判斷是否有跌倒。" in text
    assert "【重點】09:00 起裝置斷線，之後狀況不明，請通知資訊人員。" in text
    assert "都在線" not in text and check_output(text, summary, place="1 號廁所") == []


def test_json_carries_the_end_offline_time():
    from radar_agent.caregiver import report_data

    summary = summarize([*timeline("09:00:00", "09:00:35"), rec("09:00:40", "offline")])
    assert report_data(summary, place="1 號廁所")["counts"]["ended_offline"] == "2026-10-14 09:00:40"
    assert report_data(summarize(timeline("09:00:00", "09:00:35")), place="1 號廁所")["counts"]["ended_offline"] is None


# ── 時間範圍（--from、--to：Dashboard 依時間範圍要資料，docs/16）──────────

def test_cli_range_keeps_the_start_and_drops_the_end(tmp_path, capsys):
    """含開始、不含結束：相鄰兩段不重複算同一次造訪。"""
    path = tmp_path / "events.json"
    path.write_text(json.dumps(timeline("09:00:00", "09:30:00", VISITS)), encoding="utf-8")
    code, out, _ = _run(capsys, path, "--place", "1 號廁所", "--json", "--from", "2026-10-14 09:00", "--to", "2026-10-14 09:15")
    data = json.loads(out)
    assert code == 0
    assert (data["from"], data["to"], data["counts"]["visits"]) == ("2026-10-14 09:00:00", "2026-10-14 09:14:55", 1)
    code, out, _ = _run(capsys, path, "--place", "1 號廁所", "--json", "--from", "2026-10-14 09:15:00")
    data = json.loads(out)
    assert (data["from"], data["to"], data["counts"]["visits"]) == ("2026-10-14 09:15:00", "2026-10-14 09:30:00", 1)


def test_cli_range_without_records_says_cannot_judge(tmp_path, capsys):
    path = tmp_path / "events.json"
    path.write_text(json.dumps(timeline("09:00:00", "09:30:00", VISITS)), encoding="utf-8")
    code, out, _ = _run(capsys, path, "--place", "1 號廁所", "--from", "2026-10-14 13:00", "--to", "2026-10-14 15:00")
    assert (code, out) == (0, "【重點】沒有收到資料，無法判斷，請通知資訊人員。\n\n【事件摘要】\n1 號廁所：沒有收到資料，無法判斷。\n")


def test_cli_range_keeps_records_whose_time_cannot_be_read(tmp_path, capsys):
    """分不出在不在範圍內的紀錄不悄悄丟掉，照樣列為無法判讀（REVIEW.md 3.1）。"""
    path = tmp_path / "events.jsonl"
    records = [*timeline("09:00:00", "09:10:00"), {"timestamp": "昨天", "topic": TOPIC, "payload": {}}]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records), encoding="utf-8")
    code, out, _ = _run(capsys, path, "--place", "1 號廁所", "--from", "2026-10-14 09:05")
    assert code == 0
    assert out.endswith("另有 1 筆紀錄無法判讀，請通知資訊人員。\n")


def test_cli_range_rejects_unreadable_or_reversed_times(tmp_path, capsys):
    path = tmp_path / "events.json"
    path.write_text(json.dumps(timeline("09:00:00", "09:10:00")), encoding="utf-8")
    code, out, err = _run(capsys, path, "--place", "1 號廁所", "--from", "10/14 09:00")
    assert (code, out) == (2, "")
    assert "看不懂" in err
    code, out, err = _run(capsys, path, "--place", "1 號廁所", "--from", "2026-10-14 10:00", "--to", "2026-10-14 09:00")
    assert (code, out) == (2, "")
    assert "要早於結束時間" in err


# ── 【重點】與警報的後續（2026-10-29 確認，A＋B）────────────────

def test_highlights_put_alerts_without_resolution_first():
    """資料中沒有解除紀錄的警報放最前面；不說成「仍未解除」，系統只知道資料裡沒看到。"""
    records = [*timeline("09:00:00", "09:30:00"), _fall("09:02:00", "fusion", "conversation", userReply=True),
               _fall("09:10:00", "radar", "timeout", delaysec=10), _resolved("09:10:40", "noNeedHelp")]
    report = caregiver_report(summarize(records), place="1 號廁所")
    assert report.startswith("【重點】09:02 跌倒警報，資料中沒有解除紀錄，請確認是否已有人到場；跌倒警報 1 次已解除。\n")
    assert ("跌倒警報 2 次：\n"
            "　09:02 長者回覆需要協助，資料中沒有解除紀錄；\n"
            "　09:10 語音詢問 10 秒未獲回應，40 秒後長者回覆沒事，警報解除。") in report
    assert check_output(report, summarize(records), place="1 號廁所") == []


def test_a_resolution_belongs_to_the_last_alert_before_it():
    """配對規則（依 docs/05 A26 推定，待雷達負責人確認）：警報之後、下一個警報之前的第一筆解除。"""
    records = [*timeline("09:00:00", "09:30:00"), _fall("09:02:00", "radar", "timeout"),
               _fall("09:05:00", "speech", "conversation", userReply=True), _resolved("09:07:30", "standUp")]
    text = caregiver_text(summarize(records), place="1 號廁所")
    assert "跌倒警報 1 次：09:02 語音詢問未獲回應，資料中沒有解除紀錄。" in text
    assert "主動呼救 1 次：09:05，2 分 30 秒後偵測到起身，警報解除。" in text


def test_a_resolution_without_an_alert_is_still_reported():
    """沒配到警報的解除紀錄不能因為改了寫法就漏掉（REVIEW.md 3.1）。"""
    records = [*timeline("09:00:00", "09:30:00"), _resolved("09:05:00", "noNeedHelp"), _resolved("09:06:00", "standUp")]
    text = caregiver_text(summarize(records), place="1 號廁所")
    assert "另有語音回覆沒事 1 次（09:05）。" in text and "另有偵測到起身、警報解除 1 次（09:06）。" in text


def test_highlights_for_ongoing_visit_and_device_problems():
    records = [*timeline("09:00:00", "09:10:00", [("09:08:00", "09:30:00")]),
               {"timestamp": "2026-10-14 09:05:00", "topic": TOPIC, "resolution": None,
                "payload": {"event": "error", "message": "radar is not running"}}]
    report = caregiver_report(summarize(records), place="1 號廁所")
    assert report.startswith("【重點】資料結束時仍有人在內（09:08 起），請確認現況；裝置回報錯誤 1 次，請通知資訊人員。")


def test_check_flags_an_elapsed_time_that_is_not_in_the_facts():
    records = [*timeline("09:00:00", "09:30:00"), _fall("09:02:00", "radar", "timeout"), _resolved("09:05:00", "standUp")]
    summary = summarize(records)
    text = caregiver_text(summary, place="1 號廁所")
    assert "3 分鐘後偵測到起身" in text and check_output(text, summary, place="1 號廁所") == []
    assert check_output(text.replace("3 分鐘後", "7 分鐘後"), summary, place="1 號廁所") == ["數字 7 不在事實裡"]
