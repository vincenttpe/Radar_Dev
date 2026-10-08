"""甲方週報第 1 版：跌倒次數與活動量。"""
import copy
import json
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
    assert len(records) == 1
    assert len(skipped) == 4
    assert skipped[0].startswith("第 2 筆")
    assert "fall" in skipped[2]


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
    assert r["data_quality"] == {"records_in_week": 11, "out_of_week": 0, "fall_field_missing": 0, "skipped": []}


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
    r = report_of([row("2026/06/02 09:00 AM", speed="Sprint"), {"Timestamp": "2026/06/02 10:00 AM", "Motion": "x"}])
    assert r["activity"]["speed"] == {"Slow": 0, "Normal": 0, "Fast": 0, "其他": 2}


def test_data_without_fall_field_is_not_reported_as_zero_falls(repo):
    r = report_of(load(repo / "data" / "samples" / "move.json"), week=date(2025, 3, 3))
    assert r["falls"]["count"] == 0
    assert r["falls"]["judgeable"] is False  # 不能把「沒資料」當成「沒有跌倒」
    assert r["data_quality"]["fall_field_missing"] == 4
    assert r["activity"]["total"] == 4


def test_mixed_fall_field_is_counted_and_flagged():
    rows = [row("2026/06/02 09:00 AM", fall="yes"), {"Timestamp": "2026/06/02 10:00 AM", "Motion": "x", "Speed": "Slow"}]
    r = report_of(rows)
    assert r["falls"]["count"] == 1 and r["falls"]["judgeable"] is True
    assert r["data_quality"]["fall_field_missing"] == 1


def test_empty_input():
    r = report_of([])
    assert r["falls"] == {"count": 0, "events": [], "judgeable": False}
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
    assert "本週跌倒 **1** 次。" in md
    assert "2026/06/03 21:25　原始描述：A person slumped from the chair onto the floor." in md
    assert "本週動作紀錄 **10** 筆（不含跌倒）" in md
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
    assert "本週沒有動作紀錄。" in md
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
    assert "本週跌倒 **1** 次。" in out and out.startswith("# 示範機構 週報")


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
