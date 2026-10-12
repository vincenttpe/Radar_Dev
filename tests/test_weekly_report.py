"""甲方週報第 1 版：跌倒次數與活動量。"""
import copy
import json
import re
from datetime import date, datetime

import pytest

from radar_agent import weekly_report as wr

WEEK = date(2026, 6, 1)  # 星期一；樣本 2026-06-03 是星期三


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def fall_sample(repo):
    return load(repo / "data" / "samples_fall" / "2026-06-03.json")


def row(ts, fall="no", speed="Normal", motion="A person walks."):
    return {"Timestamp": ts, "Motion": motion, "Speed": speed, "fall": fall}


# ── 時間解析 ──────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("2026/06/03 08:18 AM", datetime(2026, 6, 3, 8, 18)),
    ("2026/06/03 04:44 PM", datetime(2026, 6, 3, 16, 44)),
    ("2026/06/03 12:05 AM", datetime(2026, 6, 3, 0, 5)),
    ("2026/06/03 12:30 PM", datetime(2026, 6, 3, 12, 30)),
    ("2025/03/09 17:31 PM", datetime(2025, 3, 9, 17, 31)),  # 樣本 move.json 的 24 小時制附 PM
    ("2025/03/09 17:31", datetime(2025, 3, 9, 17, 31)),
    ("2026-06-03 09:00 am", datetime(2026, 6, 3, 9, 0)),
])
def test_parse_timestamp(text, expected):
    assert wr.parse_timestamp(text) == expected


@pytest.mark.parametrize("text", ["", "not-a-date", "2026/06/03", "2026/06/03 25:00", "2026/06/03 10:61", None])
def test_parse_timestamp_rejects(text):
    with pytest.raises(ValueError):
        wr.parse_timestamp(text)


def test_every_timestamp_in_all_samples_parses(repo):
    files = sorted((repo / "data" / "samples").glob("*.json")) + sorted((repo / "data" / "samples_fall").glob("*.json"))
    for path in files:
        records, skipped = wr.from_description_records(load(path))
        assert skipped == [], path.name
        assert len(records) == len(load(path))


# ── 轉換 ─────────────────────────────────────────────────

def test_adapter_on_fall_sample(fall_sample):
    records, skipped = wr.from_description_records(fall_sample)
    assert skipped == []
    assert len(records) == 11
    assert [r.fall for r in records].count(True) == 1
    assert all(r.fall is not None for r in records)


def test_adapter_marks_missing_fall_field_as_none(repo):
    records, _ = wr.from_description_records(load(repo / "data" / "samples" / "move.json"))
    assert records and all(r.fall is None for r in records)


def test_adapter_skips_and_explains_bad_rows():
    rows = [
        row("2026/06/03 08:00 AM"),
        "not a dict",
        row("garbage"),
        row("2026/06/03 09:00 AM", fall="maybe"),
        {"Motion": "no timestamp"},
    ]
    records, skipped = wr.from_description_records(rows)
    # 時間解析不了的才略過；fall 值無法辨識的保留下來，標成無法判讀
    assert len(records) == 2
    assert len(skipped) == 3
    assert [(x.row, x.kind) for x in skipped] == [(2, "not_object"), (3, "bad_timestamp"), (5, "bad_timestamp")]
    assert skipped[0].message == "第 2 筆：不是物件"
    unreadable = records[1]
    assert unreadable.fall is None and unreadable.fall_raw == "maybe"


def test_adapter_does_not_mutate_input(fall_sample):
    before = copy.deepcopy(fall_sample)
    wr.from_description_records(fall_sample)
    assert fall_sample == before


# ── 統計 ─────────────────────────────────────────────────

def report_of(rows, week=WEEK, **kw):
    records, skipped = wr.from_description_records(rows)
    return wr.build_report(records, week, skipped, **kw)


def test_fall_sample_week(fall_sample):
    r = report_of(fall_sample)
    assert r["week_start"] == "2026-06-01" and r["week_end"] == "2026-06-07"
    assert r["falls"]["count"] == 1
    assert r["falls"]["judgeable"] is True
    assert r["falls"]["events"] == [{"at": "2026-06-03T21:25", "motion": "A person slumped from the chair onto the floor."}]
    act = r["activity"]
    assert act["total"] == 10  # 11 筆扣掉跌倒那一筆
    assert [d["count"] for d in act["per_day"]] == [0, 0, 10, 0, 0, 0, 0]
    assert act["active_days"] == 1 and act["busiest_day"] == "2026-06-03"
    assert act["speed"] == {"Slow": 0, "Normal": 5, "Fast": 5, "其他": 0}
    assert r["data_quality"] == {
        "records_in_week": 11, "out_of_week": 0, "fall_field_missing": 0, "fall_unreadable": 0,
        "bad_timestamp": 0, "not_object": 0, "unjudged_total": 0, "skipped": [],
    }
    assert r["falls"]["completeness"] == "all_readable" and r["falls"]["unreadable"] == []
    assert r["falls"]["undated"] == [] and r["falls"]["outside_week"] == []
    assert act["complete"] is True and act["unjudged"] == 0


def test_per_day_always_has_seven_entries_in_order(fall_sample):
    days = [d["date"] for d in report_of(fall_sample)["activity"]["per_day"]]
    assert days == [f"2026-06-0{n}" for n in range(1, 8)]


def test_window_is_start_inclusive_and_end_exclusive():
    rows = [
        row("2026/05/31 11:59 PM"),  # 起始日前一天，週外
        row("2026/06/01 12:00 AM"),  # 起始日 00:00，納入
        row("2026/06/07 11:59 PM"),  # 第 7 天最後一分鐘，納入
        row("2026/06/08 12:00 AM"),  # 第 8 天 00:00，週外
    ]
    r = report_of(rows)
    assert r["data_quality"]["records_in_week"] == 2
    assert r["data_quality"]["out_of_week"] == 2
    assert [d["count"] for d in r["activity"]["per_day"]] == [1, 0, 0, 0, 0, 0, 1]


def test_falls_outside_the_week_are_not_counted():
    rows = [row("2026/05/31 10:00 PM", fall="yes"), row("2026/06/02 10:00 AM", fall="yes")]
    r = report_of(rows)
    assert r["falls"]["count"] == 1
    assert r["falls"]["events"][0]["at"] == "2026-06-02T10:00"


def test_multiple_falls_listed_in_time_order():
    rows = [row("2026/06/05 09:00 AM", fall="yes"), row("2026/06/02 09:00 AM", fall="yes"), row("2026/06/03 09:00 AM")]
    r = report_of(rows)
    assert r["falls"]["count"] == 2
    assert [e["at"] for e in r["falls"]["events"]] == ["2026-06-02T09:00", "2026-06-05T09:00"]
    assert r["activity"]["total"] == 1


def test_unknown_speed_goes_to_other_bucket():
    r = report_of([row("2026/06/02 09:00 AM", speed="Sprint"), {"Timestamp": "2026/06/02 10:00 AM", "Motion": "x", "fall": "no"}])
    assert r["activity"]["speed"] == {"Slow": 0, "Normal": 0, "Fast": 0, "其他": 2}


def test_data_without_fall_field_is_not_reported_as_zero_falls(repo):
    r = report_of(load(repo / "data" / "samples" / "move.json"), week=date(2025, 3, 3))
    assert r["falls"]["count"] == 0
    assert r["falls"]["judgeable"] is False  # 不能把「沒資料」當成「沒有跌倒」
    assert r["data_quality"]["fall_field_missing"] == 4
    # 無法判讀是否為跌倒，也不能當成一般活動
    assert r["activity"]["total"] == 0 and r["activity"]["unjudged"] == 4 and r["activity"]["complete"] is False


def test_mixed_fall_field_is_counted_and_flagged():
    rows = [row("2026/06/02 09:00 AM", fall="yes"), {"Timestamp": "2026/06/02 10:00 AM", "Motion": "x", "Speed": "Slow"}]
    r = report_of(rows)
    assert r["falls"]["count"] == 1 and r["falls"]["judgeable"] is True
    assert r["data_quality"]["fall_field_missing"] == 1


def test_empty_input():
    r = report_of([])
    assert r["falls"] == {
        "count": 0, "events": [], "judgeable": False, "completeness": "none", "unreadable": [], "undated": [], "undated_marked_fall": 0, "outside_week": [],
    }
    assert r["activity"]["total"] == 0 and r["activity"]["busiest_day"] is None
    assert r["activity"]["active_days"] == 0


def test_skipped_rows_surface_in_report():
    r = report_of([row("2026/06/02 09:00 AM"), row("garbage")])
    assert len(r["data_quality"]["skipped"]) == 1
    assert "garbage" in r["data_quality"]["skipped"][0]


def test_report_is_json_serializable(fall_sample):
    json.dumps(report_of(fall_sample), ensure_ascii=False)


def test_assumptions_are_always_included_and_reference_open_questions(fall_sample):
    r = report_of(fall_sample)
    assert r["assumptions"] == wr.ASSUMPTIONS
    joined = "\n".join(r["assumptions"])
    for ref in ("A4", "A5", "A6", "C1", "C2", "C3"):
        assert ref in joined


# ── 輸出 ─────────────────────────────────────────────────

def test_markdown_for_fall_sample(fall_sample):
    md = wr.render_markdown(report_of(fall_sample, title="示範機構"))
    assert md.startswith("# 示範機構 週報　2026/06/01（一） – 2026/06/07（日）")
    assert "本次輸入的紀錄中，跌倒標記共 **1** 筆。" in md
    assert "2026/06/03 21:25　原始描述：A person slumped from the chair onto the floor." in md
    assert "本次輸入的紀錄中，動作紀錄共 **10** 筆（不含跌倒）" in md
    assert "速度分布：Slow 0、Normal 5、Fast 5。" in md
    assert "| 2026/06/03（三） | 10 |" in md
    assert "## 統計口徑與資料品質" in md
    assert "建議報告尚未包含在這一版。" in md


def test_markdown_says_undeterminable_when_no_fall_field(repo):
    md = wr.render_markdown(report_of(load(repo / "data" / "samples" / "move.json"), week=date(2025, 3, 3)))
    assert "無法判斷是否發生跌倒" in md
    assert "本週跌倒" not in md


def test_markdown_for_empty_week_does_not_claim_zero_falls():
    """沒有任何紀錄可能是裝置沒傳資料，不能寫成「跌倒 0 次」。"""
    md = wr.render_markdown(report_of([]))
    assert "本次輸入的紀錄中，沒有動作紀錄（不含跌倒）。" in md
    assert "無法判斷是否發生跌倒" in md and "也可能是裝置沒有傳資料" in md
    assert "本週跌倒" not in md


def test_markdown_lists_skipped_rows():
    md = wr.render_markdown(report_of([row("garbage")]))
    assert "無法解析 1 筆" in md and "第 1 筆" in md


# ── 命令列 ───────────────────────────────────────────────

def test_cli_markdown(repo, capsys):
    path = repo / "data" / "samples_fall" / "2026-06-03.json"
    assert wr.main(["--input", str(path), "--title", "示範機構"]) == 0
    out = capsys.readouterr().out
    assert "本次輸入的紀錄中，跌倒標記共 **1** 筆。" in out and out.startswith("# 示範機構 週報")


def test_cli_defaults_to_monday_of_latest_record(repo, capsys):
    path = repo / "data" / "samples_fall" / "2026-06-03.json"
    assert wr.main(["--input", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["week_start"] == "2026-06-01"


def test_cli_explicit_week_start_changes_window(repo, capsys):
    path = repo / "data" / "samples_fall" / "2026-06-03.json"
    assert wr.main(["--input", str(path), "--week-start", "2026-06-08", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["falls"]["count"] == 0 and out["data_quality"]["out_of_week"] == 11


def test_cli_rejects_non_array_and_empty(tmp_path, capsys):
    obj = tmp_path / "obj.json"
    obj.write_text("{}", encoding="utf-8")
    assert wr.main(["--input", str(obj)]) == 2
    empty = tmp_path / "empty.json"
    empty.write_text("[]", encoding="utf-8")
    assert wr.main(["--input", str(empty)]) == 2
    assert wr.main(["--input", str(empty), "--week-start", "2026-06-01"]) == 0



# ── 資料不完整時，不能把下限寫成全週總數 ──────────────────

def test_unreadable_fall_value_is_listed_not_dropped():
    """評估重現的情況：一筆 no、一筆 unknown。原本主文寫「跌倒 0 次」，unknown 那筆只在文末出現。"""
    rows = [
        row("2026/06/03 08:00 AM", fall="no", motion="walk"),
        row("2026/06/03 09:00 AM", fall="unknown", motion="A person slumped from the chair onto the floor."),
    ]
    r = report_of(rows)
    assert r["falls"]["count"] == 0
    assert r["falls"]["completeness"] == "partial"
    assert r["falls"]["unreadable"] == [{"at": "2026-06-03T09:00", "motion": "A person slumped from the chair onto the floor.", "raw": "unknown"}]
    assert r["data_quality"]["fall_unreadable"] == 1 and r["data_quality"]["skipped"] == []

    md = wr.render_markdown(r)
    assert "本週跌倒" not in md
    assert "可判讀的紀錄中偵測到 **0** 次跌倒；另有 1 筆無法判讀，**全週總數無法確認**。" in md
    assert "（1 筆跌倒欄位值無法辨識。）" in md
    assert "2026/06/03 09:00　fall 值 'unknown'；原始描述：A person slumped from the chair onto the floor." in md


def test_some_records_missing_the_fall_field_make_the_count_partial():
    rows = [row("2026/06/03 08:00 AM", fall="yes"), {"Timestamp": "2026/06/03 09:00 AM", "Motion": "x", "Speed": "Slow"}]
    r = report_of(rows)
    assert r["falls"]["count"] == 1 and r["falls"]["completeness"] == "partial"
    md = wr.render_markdown(r)
    assert "可判讀的紀錄中偵測到 **1** 次跌倒；另有 1 筆無法判讀，**全週總數無法確認**。" in md
    assert "（1 筆沒有跌倒欄位。）" in md


def test_unparseable_timestamps_make_the_count_partial_because_they_may_be_this_week():
    r = report_of([row("2026/06/03 08:00 AM"), row("garbage")])
    assert r["falls"]["completeness"] == "partial"
    assert "（1 筆時間無法解析，可能屬於本週。）" in wr.render_markdown(r)


def test_all_reasons_are_listed_separately():
    rows = [
        row("2026/06/03 08:00 AM", fall="no"),
        {"Timestamp": "2026/06/03 09:00 AM", "Motion": "x"},
        row("2026/06/03 10:00 AM", fall="maybe"),
        row("garbage"),
    ]
    md = wr.render_markdown(report_of(rows))
    assert "另有 3 筆無法判讀" in md
    assert "（1 筆沒有跌倒欄位、1 筆跌倒欄位值無法辨識、1 筆時間無法解析，可能屬於本週。）" in md


def test_complete_week_keeps_the_plain_headline(fall_sample):
    md = wr.render_markdown(report_of(fall_sample))
    assert "本次輸入的紀錄中，跌倒標記共 **1** 筆。" in md and "**全週總數無法確認**" not in md  # 口徑說明裡有同樣字樣，所以比對主文才有的粗體


def test_only_unreadable_values_means_none_and_still_lists_them():
    r = report_of([row("2026/06/03 09:00 AM", fall="??", motion="slumped")])
    assert r["falls"]["completeness"] == "none" and r["falls"]["judgeable"] is False
    md = wr.render_markdown(r)
    assert "本週的資料沒有可判讀的跌倒欄位，**無法判斷是否發生跌倒**。" in md
    assert "fall 值 '??'；原始描述：slumped" in md


def test_empty_week_with_unparseable_rows_says_they_may_belong_here():
    md = wr.render_markdown(report_of([row("garbage")]))
    assert "本週沒有任何紀錄，**無法判斷是否發生跌倒**" in md
    assert "（另有 1 筆時間無法解析，可能屬於本週。）" in md


# ── 評估三：數字不能比資料更確定 ───────────────────────────

def test_possible_fall_is_not_counted_as_activity():
    """跌倒一節列為「可能就是跌倒」的那筆，活動量不能再當成一般動作。"""
    rows = [
        row("2026/06/03 08:00 AM", fall="no", motion="walk"),
        row("2026/06/03 09:00 AM", fall="unknown", motion="A person slumped from the chair onto the floor."),
    ]
    r = report_of(rows)
    assert r["activity"]["total"] == 1 and r["activity"]["unjudged"] == 1 and r["activity"]["complete"] is False
    md = wr.render_markdown(r)
    assert "本週動作紀錄" not in md
    assert "可判讀的紀錄中有動作紀錄 **1** 筆（不含跌倒）；另有 1 筆無法判讀、未計入，**全週總數無法確認**。" in md


def test_activity_headline_is_partial_when_timestamps_are_unparseable():
    md = wr.render_markdown(report_of([row("2026/06/03 08:00 AM"), row("garbage")]))
    assert "可判讀的紀錄中有動作紀錄 **1** 筆（不含跌倒）；另有 1 筆無法判讀、未計入" in md
    assert "本週動作紀錄" not in md


def test_complete_week_keeps_the_plain_activity_headline(fall_sample):
    md = wr.render_markdown(report_of(fall_sample))
    assert "本次輸入的紀錄中，動作紀錄共 **10** 筆（不含跌倒）。" in md
    assert "有動作紀錄的天數 1 / 7，最多的一天是 2026/06/03（三）。" in md


def test_fall_with_bad_timestamp_is_named():
    rows = [
        row("2026/06/03 08:00 AM"),
        row("not-a-date", fall="yes", motion="slumped"),
        row("also-bad", fall="??", motion="lying"),
        row("bad-and-no", fall="no"),
    ]
    records, skipped = wr.from_description_records(rows)
    assert skipped[0].fall is True and skipped[0].motion == "slumped" and skipped[0].timestamp == "not-a-date"
    r = wr.build_report(records, WEEK, skipped)
    assert r["falls"]["undated"] == [
        {"row": 2, "timestamp": "not-a-date", "motion": "slumped", "fall": "yes"},
        {"row": 3, "timestamp": "also-bad", "motion": "lying", "fall": "??"},
    ]
    md = wr.render_markdown(r)
    assert "（3 筆時間無法解析（其中 1 筆標記為跌倒），可能屬於本週。）" in md
    assert "- 第 2 筆　時間 'not-a-date'；fall 值 'yes'；原始描述：slumped" in md
    assert "- 第 3 筆　時間 'also-bad'；fall 值 '??'；原始描述：lying" in md


def test_non_object_rows_are_not_called_timestamp_problems():
    r = report_of([row("2026/06/03 08:00 AM"), "stray string"])
    assert r["data_quality"]["not_object"] == 1 and r["data_quality"]["bad_timestamp"] == 0
    md = wr.render_markdown(r)
    assert "（1 筆不是物件（內容無法判讀），可能屬於本週。）" in md
    assert "時間無法解析，可能屬於本週" not in md


def test_fall_outside_the_chosen_week_is_named():
    """預設週是最新一筆所在週；更早的跌倒不計入本週，但要點名。"""
    rows = [row("2026/06/02 09:00 AM", fall="yes", motion="slumped"), row("2026/06/10 09:00 AM")]
    r = report_of(rows, week=date(2026, 6, 8))
    assert r["falls"]["count"] == 0 and r["falls"]["completeness"] == "all_readable"
    assert r["falls"]["outside_week"] == [{"at": "2026-06-02T09:00", "motion": "slumped"}]
    md = wr.render_markdown(r)
    assert "本週之外的跌倒紀錄 1 筆（未計入本週）：" in md
    assert "- 2026/06/02 09:00　原始描述：slumped" in md


def test_empty_week_with_out_of_week_records_points_at_the_week_start(fall_sample):
    md = wr.render_markdown(report_of(fall_sample, week=date(2026, 6, 8)))
    assert "本週沒有任何紀錄，**無法判斷是否發生跌倒**；輸入資料有 11 筆在本週之外，請確認週起始日是否正確。" in md
    assert "裝置沒有傳資料" not in md
    assert "本週之外的跌倒紀錄 1 筆（未計入本週）：" in md


# 沒有裝置在線時段的資料（會議清單 D3），輸入的紀錄都可判讀也不能寫成全週結論。
def test_a_single_readable_record_is_not_reported_as_the_whole_week():
    r = report_of([row("2026/06/03 10:00 AM")], week=date(2026, 6, 1))
    assert r["falls"]["completeness"] == "all_readable"
    md = wr.render_markdown(r)
    assert "本次輸入的紀錄中，跌倒標記共 **0** 筆。" in md
    assert "本次輸入的紀錄中，動作紀錄共 **1** 筆（不含跌倒）。" in md
    assert "本週跌倒" not in md and "本週動作紀錄" not in md
    assert "| 2026/06/01（一） | 0 |\n" in md
    assert "0 表示該日沒有納入活動統計的紀錄，不代表沒有活動，也不能據此判定裝置是否在線（會議清單 D3）。" in md


@pytest.mark.parametrize("fall", ["yes", "unknown"])
def test_daily_zero_note_does_not_claim_no_record_was_received(fall):
    """當天只收到跌倒或無法判讀的紀錄，逐日表格仍是 0（不計入活動量）；說明不能寫成「沒有收到紀錄」。"""
    md = wr.render_markdown(report_of([row("2026/06/03 10:00 AM", fall=fall)], week=date(2026, 6, 1)))
    assert "| 2026/06/03（三） | 0 |" in md
    assert "沒有收到紀錄" not in md
    assert "0 表示該日沒有納入活動統計的紀錄，不代表沒有活動，也不能據此判定裝置是否在線（會議清單 D3）。" in md


# ── JSON 與 Markdown 的數字要一致 ─────────────────────────────

def test_unjudged_count_is_the_same_in_json_and_markdown():
    """時間無法解析的紀錄也是「無法判讀、未計入」：JSON 的 activity.unjudged 與 Markdown 都寫 2。"""
    r = report_of([row("2026/06/03 08:00 AM"), row("2026/06/03 09:00 AM", fall="maybe"), row("garbage")])
    assert r["activity"]["unjudged"] == r["data_quality"]["unjudged_total"] == 2
    assert "另有 2 筆無法判讀、未計入，**全週總數無法確認**。" in wr.render_markdown(r)


def test_active_days_counts_only_days_with_activity_and_says_so():
    """只收到跌倒或無法判讀紀錄的日子不算，標籤寫明是「有動作紀錄的天數」，和逐日表格同一口徑。"""
    r = report_of([
        row("2026/06/03 10:00 AM", fall="yes"),
        row("2026/06/04 10:00 AM", fall="unknown"),
        row("2026/06/05 10:00 AM", fall="no"),
    ])
    assert r["activity"]["active_days"] == 1
    md = wr.render_markdown(r)
    assert "有動作紀錄的天數 1 / 7，最多的一天是 2026/06/05（五）。" in md
    assert "有紀錄的天數" not in md


def test_busiest_day_tie_takes_the_earliest_day():
    r = report_of([row("2026/06/05 10:00 AM"), row("2026/06/03 10:00 AM")])
    assert r["activity"]["busiest_day"] == "2026-06-03"


_CONSISTENCY_CASES = {
    "全部可判讀": [row("2026/06/03 08:00 AM"), row("2026/06/04 08:00 AM"), row("2026/06/04 09:00 AM", fall="yes")],
    "部分無法判讀": [row("2026/06/03 08:00 AM"), row("2026/06/03 09:00 AM", fall="maybe"), row("2026/06/05 10:00 AM", fall=None)],
    "時間無法解析": [row("2026/06/03 08:00 AM"), row("garbage"), row("bad", fall="yes"), 5],
    "只有跌倒與無法判讀": [row("2026/06/03 08:00 AM", fall="yes"), row("2026/06/04 08:00 AM", fall="zz")],
    "空週": [],
    "只有週外資料": [row("2026/06/20 08:00 AM"), row("2026/06/21 08:00 AM", fall="yes")],
}


@pytest.mark.parametrize("name", list(_CONSISTENCY_CASES))
def test_every_number_in_markdown_matches_the_json(name):
    """Markdown 印出的數字都要等於 JSON 的欄位；兩邊各算各的就會像 unjudged 那樣不一致。"""
    r = report_of(_CONSISTENCY_CASES[name])
    md = wr.render_markdown(r)
    falls, act, quality = r["falls"], r["activity"], r["data_quality"]
    for m in re.finditer(r"跌倒標記共 \*\*(\d+)\*\* 筆|偵測到 \*\*(\d+)\*\* 次跌倒", md):
        assert int(m.group(1) or m.group(2)) == falls["count"], name
    for m in re.finditer(r"動作紀錄共 \*\*(\d+)\*\* 筆|有動作紀錄 \*\*(\d+)\*\* 筆", md):
        assert int(m.group(1) or m.group(2)) == act["total"], name
    for m in re.finditer(r"另有 (\d+) 筆無法判讀", md):
        assert int(m.group(1)) == quality["unjudged_total"] == act["unjudged"], name
    for m in re.finditer(r"有動作紀錄的天數 (\d+) / 7", md):
        assert int(m.group(1)) == act["active_days"], name
    for m in re.finditer(r"（其中 (\d+) 筆標記為跌倒）", md):
        assert int(m.group(1)) == falls["undated_marked_fall"], name
    rows = re.findall(r"^\| \d{4}/\d{2}/\d{2}（.） \| (\d+) \|$", md, re.MULTILINE)
    assert [int(n) for n in rows] == [d["count"] for d in act["per_day"]], name
