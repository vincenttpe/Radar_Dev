"""居服員 Profile 的範本與輸出檢查（docs/02「依 Profile 改寫」範本層、輸出檢查）。

只透過 caregiver_text() 與 check_output() 驗證；期望的句子是在對話中確認過的寫法（2026-10-14）。
"""
from radar_agent.caregiver import caregiver_text, check_output
from radar_agent.device_events import summarize

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
        "1 號廁所 10/14：有人使用 2 次，最長一次 8 分鐘。跌倒警報 0 次。久待警報 0 次。這段時間裝置都在線。"
    )


def test_disconnection_is_stated_and_zero_falls_is_limited_to_online_time():
    """斷線期間不能寫成 0 次跌倒（REVIEW.md 3.1 沒有資料不等於 0）。"""
    records = timeline("09:00:00", "09:10:00", [("09:02:00", "09:03:00")]) + timeline("09:40:00", "09:50:00")
    assert caregiver_text(summarize(records), place="1 號廁所") == (
        "1 號廁所 10/14：有人使用 1 次，最長一次 1 分鐘。在線期間跌倒警報 0 次。在線期間久待警報 0 次。"
        "09:10–09:40 裝置斷線，這段時間無法判斷是否有跌倒。"
    )


def test_no_data_at_all_says_cannot_judge():
    assert caregiver_text(summarize([]), place="1 號廁所") == "1 號廁所：沒有收到資料，無法判斷。"


def test_online_without_visits_says_no_use_detected():
    assert caregiver_text(summarize(timeline("09:00:00", "09:10:00")), place="1 號廁所") == (
        "1 號廁所 10/14：沒有偵測到有人使用。跌倒警報 0 次。久待警報 0 次。這段時間裝置都在線。"
    )


def test_alerts_list_their_times_and_no_need_help_is_separate():
    """C2：跌倒警報次數與語音回覆沒事次數分開列；主動呼救（DistressCall）標出來。"""
    records = timeline("09:00:00", "09:50:00", [("09:04:00", "09:08:00"), ("09:19:00", "09:46:00")])
    records += [
        rec("09:05:00", "fall", trigger="timeout", userReply=False, confidence=100),
        rec("09:05:20", "resolved", trigger="noNeedHelp", mode="speech", userReply=True, confidence=100),
        rec("09:20:00", "fall", trigger="timeout", mode="speech", userReply=False, confidence=100),
        rec("09:45:00", "longstay", trigger="timeout", delaysec=600, confidence=100),
    ]
    assert caregiver_text(summarize(records), place="1 號廁所") == (
        "1 號廁所 10/14：有人使用 2 次，最長一次 27 分鐘。"
        "跌倒警報 2 次（09:05、09:20；含主動呼救 1 次）。語音回覆沒事 1 次。"
        "久待警報 1 次（09:45）。這段時間裝置都在線。"
    )


def test_interrupted_and_ongoing_visits_are_not_dropped():
    records = (timeline("09:00:00", "09:10:00", [("09:08:00", "09:30:00")])
               + timeline("09:40:00", "09:50:00", [("09:45:00", "10:30:00")]))
    assert caregiver_text(summarize(records), place="1 號廁所") == (
        "1 號廁所 10/14：沒有偵測到有人使用。另有 1 次使用途中裝置斷線，停留時間不明。"
        "資料結束時仍有人在內（09:45 起）。"
        "在線期間跌倒警報 0 次。在線期間久待警報 0 次。"
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
    assert text.endswith("裝置回報錯誤 1 次（09:05），請通知資訊人員。另有 1 筆紀錄無法判讀，請通知資訊人員。")


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


def test_check_flags_medical_wording_and_false_alarm():
    summary = summarize(timeline("09:00:00", "09:30:00", ONLINE_TWO_VISITS))
    text = caregiver_text(summary, place="1 號廁所") + "建議就醫診斷。其中 1 次是誤報。"
    problems = check_output(text, summary, place="1 號廁所")
    assert "不能出現「診斷」（非醫療器材）" in problems
    assert "不能寫「誤報」（C2：語音回覆沒事不直接算誤報）" in problems


def test_check_flags_zero_falls_when_device_was_offline():
    """REVIEW.md 3.1：沒有資料不等於 0。"""
    summary = summarize(timeline("09:00:00", "09:10:00") + timeline("09:40:00", "09:50:00"))
    text = caregiver_text(summary, place="1 號廁所").replace("在線期間跌倒警報", "跌倒警報")
    assert check_output(text, summary, place="1 號廁所") == ["裝置有斷線，不能寫「跌倒警報 0 次」而不註明在線期間"]


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
    longest_90s = summarize(timeline("09:00:00", "09:10:00", [("09:01:00", "09:02:30")]))
    assert "最長一次 1 分 30 秒。" in caregiver_text(longest_90s, place="1 號廁所")
    longest_45s = summarize(timeline("09:00:00", "09:10:00", [("09:01:00", "09:01:45")]))
    assert "最長一次 45 秒。" in caregiver_text(longest_45s, place="1 號廁所")


def test_data_spanning_two_days_shows_the_date_range():
    records = [rec("23:59:50", "absence", day="2026-10-13"), rec("23:59:55", "absence", day="2026-10-13"),
               rec("00:00:00", "absence"), rec("00:00:05", "absence")]
    assert caregiver_text(summarize(records), place="1 號廁所").startswith("1 號廁所 10/13–10/14：")
