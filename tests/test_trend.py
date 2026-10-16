"""使用趨勢：近 7 天與前 7 天比較（docs/11 G3）。只透過 usage_trend()、trend_text()、check_trend() 與命令列驗證。

測試資料用 25 秒一筆心跳（不超過 30 秒就不算斷線），一天約 3,500 筆，比真實的 5 秒少，跑得快。
"""
import importlib.util
import json
from datetime import date, datetime, timedelta

import pytest

from radar_agent.caregiver import check_trend, main, trend_text, usage_trend

TOPIC = "radar10/DEMO-SIM-01"
STEP = timedelta(seconds=25)
FIRST = date(2026, 10, 1)


def _rec(when: datetime, event: str) -> dict:
    payload = {"event": event} if event in ("online", "offline") else {
        "mode": "radar", "event": event, "trigger": "track", "userReply": False}
    return {"timestamp": when.strftime("%Y-%m-%d %H:%M:%S"), "topic": TOPIC, "resolution": None, "payload": payload}


def day_records(day: date, visits: list[str], *, offline: tuple[str, str] | None = None) -> list[dict]:
    """一整天的紀錄；visits 是每次使用的開始時刻（HH:MM），每次 4 分鐘。"""
    start = datetime.combine(day, datetime.min.time())
    begins = [datetime.combine(day, datetime.strptime(v, "%H:%M").time()) for v in visits]
    off = on = None
    if offline:
        off, on = (datetime.combine(day, datetime.strptime(t, "%H:%M").time()) for t in offline)
    present: dict[datetime, str] = {}
    for b in begins:
        t = b
        while t < b + timedelta(minutes=4):
            present[t] = "presence" if t == b else "moving"
            t += STEP
    out, now = [], start
    while now < start + timedelta(days=1):
        if off is None or not off <= now < on:
            out.append(_rec(now, present.get(now, "absence")))
        now += STEP
    return out


def two_weeks(per_day: list[list[str]], **offline_on: tuple[str, str]) -> list[dict]:
    records = []
    for i, visits in enumerate(per_day):
        records += day_records(FIRST + timedelta(days=i), visits, offline=offline_on.get(f"d{i}"))
    return records


SIX = ["07:00", "09:00", "11:00", "14:00", "17:00", "20:00"]


def test_dawn_use_increase_is_reported_and_daytime_stays_the_same():
    """每日多 2 次但只是前一週的 29%，不到 30%，所以每日總次數寫「差不多」。"""
    per_day = [[*SIX, "02:00"]] * 7 + [[*SIX, "01:00", "02:30", "04:00"]] * 7
    trend = usage_trend(two_weeks(per_day))
    text = trend_text(trend, place="1 號廁所")
    assert text.splitlines()[:3] == [
        "【使用趨勢】1 號廁所 10/8–10/14 與前一週（10/1–10/7）比較：",
        "每日使用次數：平均每日 9.0 次，前一週 7.0 次，差不多。",
        "凌晨（00:00–06:00）使用次數：平均每日 3.0 次，前一週 1.0 次，增加。",
    ]
    assert check_trend(text, trend, place="1 號廁所") == []


def test_small_differences_are_reported_as_about_the_same():
    """差距不到 1 次寫「差不多」。"""
    per_day = [SIX] * 7 + [[*SIX, "15:00"]] * 3 + [SIX] * 4      # 平均多 0.4 次
    text = trend_text(usage_trend(two_weeks(per_day)), place="1 號廁所")
    assert "每日使用次數：平均每日 6.4 次，前一週 6.0 次，差不多。" in text


def test_daily_total_uses_30_percent_and_dawn_uses_50_percent():
    """門檻分開（2026-10-16 決定）：每日總次數 30%，凌晨 50%。"""
    six_more = [*SIX, "21:00", "22:00"]                       # 6 → 8 次：多 33%，達 30%
    per_day = [SIX] * 7 + [six_more] * 7
    text = trend_text(usage_trend(two_weeks(per_day)), place="1 號廁所")
    assert "每日使用次數：平均每日 8.0 次，前一週 6.0 次，增加。" in text

    dawn3, dawn4 = ["01:00", "02:30", "04:00"], ["00:30", "01:30", "03:00", "05:00"]
    per_day = [[*SIX, *dawn3]] * 7 + [[*SIX, *dawn4]] * 7     # 凌晨 3 → 4：多 33%，不到 50%
    text = trend_text(usage_trend(two_weeks(per_day)), place="1 號廁所")
    assert "凌晨（00:00–06:00）使用次數：平均每日 4.0 次，前一週 3.0 次，差不多。" in text

    dawn2 = ["01:00", "03:00"]
    per_day = [[*SIX, *dawn2]] * 7 + [[*SIX, *dawn3]] * 7     # 凌晨 2 → 3：多 50%，剛好達到
    text = trend_text(usage_trend(two_weeks(per_day)), place="1 號廁所")
    assert "凌晨（00:00–06:00）使用次數：平均每日 3.0 次，前一週 2.0 次，增加。" in text


def test_from_zero_any_increase_of_at_least_one_counts():
    per_day = [SIX] * 7 + [[*SIX, "03:00"]] * 7
    text = trend_text(usage_trend(two_weeks(per_day)), place="1 號廁所")
    assert "凌晨（00:00–06:00）使用次數：平均每日 1.0 次，前一週 0.0 次，增加。" in text


def test_decrease_is_reported():
    per_day = [[*SIX, "01:00", "03:00"]] * 7 + [SIX] * 7
    text = trend_text(usage_trend(two_weeks(per_day)), place="1 號廁所")
    assert "凌晨（00:00–06:00）使用次數：平均每日 0.0 次，前一週 2.0 次，減少。" in text


def test_a_day_with_too_little_online_time_is_left_out_not_counted_as_zero():
    """沒有資料不等於沒有使用（REVIEW.md 3.1）。"""
    per_day = [SIX] * 14
    text = trend_text(usage_trend(two_weeks(per_day, d9=("12:00", "15:00"))), place="1 號廁所")
    assert "10/10 在線不足，不列入" in text
    assert "每日使用次數：平均每日 6.0 次，前一週 6.0 次，差不多。" in text


def test_too_few_counted_days_means_no_comparison():
    per_day = [SIX] * 14
    offline = {f"d{i}": ("12:00", "15:00") for i in range(7, 10)}
    trend = usage_trend(two_weeks(per_day, **offline))
    text = trend_text(trend, place="1 號廁所")
    assert "可比較的天數不足（近一週 4 天、前一週 7 天，各需 5 天），無法比較。" in text
    assert "差不多" not in text and "增加" not in text
    assert check_trend(text, trend, place="1 號廁所") == []


def test_missing_days_before_the_data_starts_are_not_counted():
    """只有一週的資料：前一週每天都沒有資料，無法比較。"""
    records = []
    for i in range(7):
        records += day_records(FIRST + timedelta(days=i), SIX)
    text = trend_text(usage_trend(records), place="1 號廁所")
    assert "可比較的天數不足（近一週 7 天、前一週 0 天，各需 5 天），無法比較。" in text


def test_records_with_unreadable_time_are_reported():
    records = [*two_weeks([SIX] * 14), {"timestamp": "昨天", "topic": TOPIC, "resolution": None, "payload": {}}]
    text = trend_text(usage_trend(records), place="1 號廁所")
    assert "另有 1 筆紀錄時間無法判讀，請通知資訊人員。" in text


def test_no_data_at_all_says_cannot_judge():
    assert trend_text(usage_trend([]), place="1 號廁所") == "【使用趨勢】1 號廁所：沒有收到資料，無法判斷。"


def test_check_flags_a_number_not_in_the_statistics_and_medical_wording():
    trend = usage_trend(two_weeks([SIX] * 14))
    text = trend_text(trend, place="1 號廁所")
    assert check_trend(text + "建議就醫診斷，約 99 次。", trend, place="1 號廁所") == [
        "數字 99 不在統計裡", "不能出現「診斷」（非醫療器材）",
    ]


@pytest.fixture(scope="module")
def two_week_demo(repo, tmp_path_factory):
    spec = importlib.util.spec_from_file_location("demo_generate", repo / "data" / "samples_demo" / "generate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path_factory.mktemp("demo") / "bathroom_two_weeks.jsonl"
    path.write_text(module.to_jsonl(module.generate_two_weeks()), encoding="utf-8")
    return path


def test_cli_trend_on_the_two_week_demo_data(two_week_demo, capsys):
    """Demo 要展示的內容（data/samples_demo/generate.py --two-weeks）；用語一改這裡就會失敗。"""
    assert main([str(two_week_demo), "--place", "1 號廁所", "--trend"]) == 0
    assert capsys.readouterr().out == (
        "【使用趨勢】1 號廁所 10/8–10/14 與前一週（10/1–10/7）比較：\n"
        "每日使用次數：平均每日 7.8 次，前一週 6.9 次，差不多。\n"
        "凌晨（00:00–06:00）使用次數：平均每日 2.0 次，前一週 0.9 次，增加。\n"
        "近 7 天：10/8 7 次（凌晨 1）、10/9 6 次（凌晨 1）、10/10 在線不足，不列入、10/11 7 次（凌晨 1）、"
        "10/12 9 次（凌晨 3）、10/13 8 次（凌晨 3）、10/14 10 次（凌晨 3）。\n"
        "這是場域的統計，沒有個人 ID，不代表特定長者；供照護人員參考。\n"
    )


def test_cli_trend_with_a_medical_word_in_the_place_prints_nothing(tmp_path, capsys):
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in two_weeks([SIX] * 14)), encoding="utf-8")
    assert main([str(path), "--place", "治療室", "--trend"]) == 1
    out, err = capsys.readouterr()
    assert out == "" and "治療" in err
