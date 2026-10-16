"""實測資料的檢查與遮罩（radar_agent.field_data，docs/11「10/19 實測資料」）。

測試用的 MAC 是虛構的（02:00:00 開頭的本地管理位址格式），不是任何真實裝置。
"""
import json

import pytest

from radar_agent.field_data import MASKED, check_out_dir, inspect, main, mask, report_text

FAKE_MAC = "02AB34CD56EF"
OTHER_MAC = "02AB34CD5700"


def rec(t: str, event: str, device: str = FAKE_MAC, **payload) -> dict:
    return {"timestamp": f"2026-10-19 {t}", "topic": f"radar10/{device}", "resolution": None,
            "payload": {"event": event, **payload}}


@pytest.fixture
def records():
    return [
        rec("09:00:00", "online"),
        rec("09:00:05", "presence"),
        rec("09:00:10", "moving"),
        rec("09:00:15", "fall", mode="radar", trigger="timeout", delaysec=10),
        rec("09:00:15", "fall", mode="radar", trigger="timeout", delaysec=10),  # 完全重複
        rec("09:00:20", "resolved", trigger="standUp"),
        rec("09:00:25", "absence"),
        rec("09:05:00", "absence"),  # 中間 4 分 35 秒沒資料
        {"timestamp": "2026/10/19 09:06", "topic": f"radar10/{FAKE_MAC}", "resolution": None, "payload": {"event": "absence"}},
        {"timestamp": "2026-10-19 09:06:00", "topic": f"radar10/{FAKE_MAC}/config/set", "resolution": None,
         "payload": {"params": {"note": f"for 02:ab:34:cd:56:ef and {FAKE_MAC}"}}},
    ]


def test_inspect_reports_counts_gaps_and_hours_without_printing_the_mac(records):
    text = report_text(inspect(records, "一行一筆（JSON Lines）"))
    assert FAKE_MAC not in text
    lines = text.splitlines()
    assert lines[0] == "格式：一行一筆（JSON Lines），10 筆（完全相同的重複 1 筆）"
    assert lines[1] == "裝置：1 台（（識別碼像 MAC，不顯示） 9 筆）"
    assert "伺服器下給裝置的指令（topic 多層）：1 筆，不算進事件" in lines
    assert "時間戳讀不懂（不是 %Y-%m-%d %H:%M:%S）：1 筆" in lines
    assert "時間範圍：2026-10-19 09:00:00 ～ 2026-10-19 09:05:00（時間戳沒有時區，照原樣）" in lines
    assert "事件：absence 2、fall（radar） 1、moving 1、online 1、presence 1、resolved 1" in lines
    assert "超過 30 秒沒資料：1 段，最長 4 分 35 秒（10-19 09:00:25 起）" in lines
    hours = next(line for line in lines if line.startswith("每小時有人"))
    assert " 09:1 " in hours and " 08:0 " in hours


def test_inspect_names_every_device_and_says_they_must_be_split(records):
    records.append(rec("09:01:00", "absence", device="DEMO-SIM-01"))
    text = report_text(inspect(records, "JSON 陣列"))
    assert "裝置：2 台" in text and "DEMO-SIM-01 1 筆" in text
    assert "mask 會把每台裝置分成一個檔案" in text


def test_inspect_with_no_readable_time():
    text = report_text(inspect([{"payload": {}}, {"topic": "radar10/X", "timestamp": "?"}], "JSON 陣列"))
    assert "沒有 topic：1 筆" in text and text.endswith("時間範圍：沒有讀得懂的時間戳")


def test_inspect_without_gaps():
    text = report_text(inspect([rec("09:00:00", "absence", device="X"), rec("09:00:05", "absence", device="X")], "JSON 陣列"))
    assert "超過 30 秒沒資料：沒有" in text and "MAC" not in text.split("每小時")[0]


def test_mask_replaces_every_device_id_and_mac_like_string(records):
    records.append(rec("09:01:00", "absence", device=OTHER_MAC))
    records.append({"payload": {"event": "absence"}})  # 沒有 topic，分不到裝置
    by_device, unshifted = mask(records)
    assert sorted(by_device) == ["FIELD-01", "FIELD-02"]
    assert unshifted == 0
    text = json.dumps(by_device, ensure_ascii=False)
    assert FAKE_MAC not in text and OTHER_MAC not in text and "02:ab:34" not in text.lower()
    command = by_device["FIELD-01"][-1]
    assert command["topic"] == "radar10/FIELD-01/config/set"
    assert command["payload"]["params"]["note"] == f"for {MASKED} and FIELD-01"
    assert sum(len(rs) for rs in by_device.values()) == len(records) - 1


def test_mask_does_not_replace_a_short_device_id_inside_other_text():
    by_device, _ = mask([rec("09:00:00", "absence", device="A1", note="A1 是房號")])
    assert by_device["FIELD-01"][0]["payload"]["note"] == "A1 是房號"


def test_mask_can_shift_timestamps_for_a_utc_source(records):
    by_device, unshifted = mask(records, shift_hours=8)
    times = [r["timestamp"] for r in by_device["FIELD-01"]]
    assert times[0] == "2026-10-19 17:00:00"
    assert "2026/10/19 09:06" in times  # 讀不懂的時間照原樣，並回報
    assert unshifted == 1


def test_out_dir_must_be_outputs_inside_the_repo(repo, tmp_path):
    assert check_out_dir(repo / "outputs" / "field") is None
    assert check_out_dir(tmp_path / "field") is None or (tmp_path / ".git").exists()
    for bad in (repo / "data" / "field", repo, repo / "demo"):
        assert "不能放進會被 commit 的地方" in check_out_dir(bad)


def test_cli_inspect_and_mask(repo, tmp_path, records, capsys, monkeypatch):
    src = tmp_path / "field.jsonl"
    src.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    assert main(["inspect", str(src)]) == 0
    assert FAKE_MAC not in capsys.readouterr().out

    assert main(["mask", str(src), "--out-dir", str(repo / "data" / "field")]) == 2
    assert "不能放進會被 commit 的地方" in capsys.readouterr().err
    assert not (repo / "data" / "field").exists()

    out = repo / "outputs" / "test_field_data"
    try:
        assert main(["mask", str(src), "--out-dir", str(out), "--shift-hours", "8"]) == 0
        printed = capsys.readouterr().out
        assert "FIELD-01.jsonl（10 筆）" in printed  # 重複的紀錄照樣保留，去重交給 summarize
        assert "時間戳已平移 +8 小時；1 筆時間讀不懂，沒有平移" in printed
        assert FAKE_MAC not in printed and FAKE_MAC not in (out / "FIELD-01.jsonl").read_text(encoding="utf-8")
    finally:
        for f in out.glob("*"):
            f.unlink()
        out.rmdir()


def test_cli_mask_reports_records_without_topic(repo, tmp_path, capsys):
    src = tmp_path / "field.json"
    src.write_text(json.dumps([rec("09:00:00", "absence"), {"payload": {}}]), encoding="utf-8")
    out = repo / "outputs" / "test_field_data_2"
    try:
        assert main(["mask", str(src), "--out-dir", str(out)]) == 0
        assert "沒有 topic、分不到裝置而捨棄：1 筆" in capsys.readouterr().out
    finally:
        for f in out.glob("*"):
            f.unlink()
        out.rmdir()


def test_cli_rejects_unreadable_files(tmp_path, capsys):
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{}\nnot json\n", encoding="utf-8")
    assert main(["inspect", str(bad)]) == 2
    assert "第 2 行不是 JSON" in capsys.readouterr().err
    assert main(["inspect", str(tmp_path / "missing.json")]) == 2


# ── 實機操演紀錄表與比對（compare）────────────────────────
from datetime import datetime, timedelta  # noqa: E402

from radar_agent.field_data import SCENARIOS, compare, comparison_text, read_sheet, sheet_text  # noqa: E402

DRILL_DAY = "2026-10-19"
FALL = {"event": "fall", "confidence": 100}


def _t(hms: str) -> datetime:
    return datetime.strptime(f"{DRILL_DAY} {hms}", "%Y-%m-%d %H:%M:%S")


def drill_records(*, shift_hours: int = 0, device: str = "FIELD-01", events: dict | None = None,
                  gap: tuple[str, str] = ("10:25:10", "10:29:00")) -> list[dict]:
    """10:00～10:32 每 5 秒一筆的操演資料；有人的時段送 presence／moving（久待那段送 static）。"""
    spans = [("10:01:10", "10:03:00", "moving"), ("10:05:05", "10:06:30", "moving"), ("10:08:05", "10:10:00", "moving"),
             ("10:11:05", "10:12:30", "moving"), ("10:14:05", "10:15:30", "moving"), ("10:17:05", "10:19:30", "moving"),
             ("10:21:05", "10:23:00", "static")]
    if events is None:
        events = {
            "10:05:20": {**FALL, "mode": "radar", "trigger": "timeout", "delaysec": 10},
            "10:05:50": {"event": "resolved", "mode": "speech", "trigger": "noNeedHelp", "userReply": True},
            "10:08:20": {**FALL, "mode": "fusion", "trigger": "conversation", "userReply": True},
            "10:09:30": {"event": "resolved", "mode": "radar", "trigger": "standUp"},
            "10:11:15": {**FALL, "mode": "speech", "trigger": "conversation", "userReply": True},
            "10:12:00": {"event": "resolved", "mode": "radar", "trigger": "standUp"},
            "10:14:20": {**FALL, "mode": "speech", "trigger": "timeout", "delaysec": 10},
            "10:17:20": {**FALL, "mode": "radar", "trigger": "timeout", "delaysec": 10},
            "10:17:40": {**FALL, "mode": "radar", "trigger": "timeout", "delaysec": 10},
            "10:18:00": {**FALL, "mode": "radar", "trigger": "timeout", "delaysec": 10},
            "10:19:00": {"event": "resolved", "mode": "radar", "trigger": "standUp"},
            "10:22:10": {"event": "longstay", "mode": "radar", "trigger": "timeout", "delaysec": 60},
        }
    shift = timedelta(hours=shift_hours)
    out, now = [], _t("10:00:00")
    while now <= _t("10:32:00"):
        hms = f"{now:%H:%M:%S}"
        if not (gap and _t(gap[0]) <= now < _t(gap[1])):
            state = "absence"
            for a, b, inside in spans:
                if now == _t(a):
                    state = "presence"
                elif _t(a) < now < _t(b):
                    state = inside
            out.append({"timestamp": f"{now + shift:%Y-%m-%d %H:%M:%S}", "topic": f"radar10/{device}", "resolution": None,
                        "payload": {"mode": "radar", "event": state, "trigger": "track", "userReply": False}})
        if hms in events:
            out.append({"timestamp": f"{now + shift:%Y-%m-%d %H:%M:%S}", "topic": f"radar10/{device}", "resolution": None,
                        "payload": events[hms]})
        now += timedelta(seconds=5)
    return out


WATCH = ["10:00:00", "10:01:00", "10:05:00", "10:08:00", "10:11:00", "10:14:00", "10:17:00", "10:21:00", "10:25:00", "10:31:00"]


def drill_sheet(times: list[str] = WATCH, *, with_date: bool = False) -> str:
    rows = ["項目,狀況,手錶時間,備註"]
    for k, t in zip(SCENARIOS, times, strict=True):
        rows.append(f"{k},{SCENARIOS[k].name},{DRILL_DAY + ' ' + t if with_date and t else t},")
    return "\n".join(rows) + "\n"


def test_sheet_lists_the_ten_drill_items():
    lines = sheet_text().splitlines()
    assert lines[0] == "項目,狀況,手錶時間,備註"
    assert [line.split(",")[0] for line in lines[1:]] == [str(n) for n in range(10)]
    assert read_sheet(sheet_text(), None)[0].at is None


def test_every_item_matches_a_drill_that_went_as_planned():
    result = compare(read_sheet(drill_sheet(), datetime(2026, 10, 19).date()), drill_records())
    assert result.offset_hours == 0
    assert [c.status for c in result.checks] == ["符合"] * 10
    text = comparison_text(result)
    assert "資料時間戳與手錶同一個時區" in text
    assert "[符合] 2 A：疑似跌倒、無回應、回覆沒事　手錶 10:05:00　資料 10:05:20（+20 秒）" in text
    assert "  處置提示：10:05 請立即到場確認。" in text
    assert "  處置提示：10:05 長者回覆沒事，警報已解除；可視情況前往關心。" in text
    assert "  原始 fall 訊息 3 筆，合併成 1 則跌倒警報" in text
    assert "結果：符合 10" in text and "時間差中位數 +" in text


def test_utc_timestamps_are_detected_from_the_watch_times():
    result = compare(read_sheet(drill_sheet(with_date=True), None), drill_records(shift_hours=-8))
    assert result.offset_hours == -8
    assert all(c.status == "符合" for c in result.checks)
    assert "資料時間戳比手錶早 8 小時才對得上" in comparison_text(result)
    assert "--shift-hours 8" in comparison_text(result)


def test_unexpected_events_are_reported_as_mismatch_not_forced_to_fit():
    events = {"10:08:20": {**FALL, "mode": "radar", "trigger": "timeout", "delaysec": 10},   # 預期是 fusion
              "10:09:30": {"event": "resolved", "mode": "radar", "trigger": "standUp"}}
    result = compare(read_sheet(drill_sheet(), datetime(2026, 10, 19).date()), drill_records(events=events, gap=None))
    by_item = {c.step.item: c for c in result.checks}
    assert by_item["3"].status == "不符"
    assert by_item["3"].actual.startswith("跌倒警報（radar／timeout）")
    assert by_item["2"].status == "不符" and by_item["2"].actual == "有人使用 1 次"
    assert by_item["7"].status == "不符" and by_item["8"].status == "不符"
    assert "不要改預期去湊" in comparison_text(result)


def test_items_not_performed_and_windows_without_data():
    times = list(WATCH)
    times[5] = ""                    # C' 沒有操演
    times[9] = "10:50:00"            # 結束對時太晚，沒有資料
    result = compare(read_sheet(drill_sheet(times), datetime(2026, 10, 19).date()), drill_records())
    by_item = {c.step.item: c for c in result.checks}
    assert by_item["5"].status == "沒有操演"
    assert by_item["9"].status == "找不到資料"
    text = comparison_text(result)
    assert "[沒有操演] 5 C'：主動呼救後無回應　手錶 —　資料 —" in text
    assert "沒有操演 1" in text and "找不到資料 1" in text
    late = compare(read_sheet(drill_sheet(["10:40:00"] + [""] * 9), datetime(2026, 10, 19).date()), drill_records())
    assert late.checks[0].status == "找不到資料"
    # 斷線期間（10:25:10～10:29:00）裡的一段：10:25:30～10:27:00 沒有任何資料
    gap = compare(read_sheet("項目,狀況,手錶時間,備註\n1,一般使用,10:26:30,\n9,結束對時,10:28:00,\n", datetime(2026, 10, 19).date()),
                  drill_records())
    assert gap.checks[0].status == "找不到資料" and gap.checks[0].actual == "這一段沒有資料"


@pytest.mark.parametrize("text,day,message", [
    ("a,b,c\n", None, "第一行要是"),
    ("項目,狀況,手錶時間,備註\n11,其他,10:00:00,\n", None, "不在 0～9 之內"),
    ("項目,狀況,手錶時間,備註\n1,一般使用,十點,\n", None, "要寫成"),
    ("項目,狀況,手錶時間,備註\n1,一般使用,10:00:00,\n", None, "--date"),
    ("項目,狀況,手錶時間,備註\n1,一般使用,10:05:00,\n2,A,10:01:00,\n", "2026-10-19", "由早到晚"),
])
def test_sheet_errors(text, day, message):
    with pytest.raises(ValueError, match=message):
        read_sheet(text, datetime.fromisoformat(day).date() if day else None)


def test_sheet_accepts_bom_blank_rows_and_notes():
    steps = read_sheet("﻿項目,狀況,手錶時間,備註\n\n1,一般使用,2026-10-19 10:01:00,走了兩圈\n", None)
    assert steps[0].note == "走了兩圈" and steps[0].at == _t("10:01:00")
    result = compare(steps, drill_records())
    assert "  備註：走了兩圈" in comparison_text(result)


def test_cli_sheet_and_compare(tmp_path, capsys):
    assert main(["sheet"]) == 0
    assert capsys.readouterr().out == sheet_text()
    sheet, data = tmp_path / "drill.csv", tmp_path / "FIELD-01.jsonl"
    sheet.write_text(drill_sheet(), encoding="utf-8")
    data.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in drill_records()), encoding="utf-8")
    assert main(["compare", str(sheet), str(data), "--date", DRILL_DAY]) == 0
    assert "結果：符合 10" in capsys.readouterr().out
    data.write_text("".join(json.dumps(r) + "\n" for r in drill_records(events={}, gap=None)), encoding="utf-8")
    assert main(["compare", str(sheet), str(data), "--date", DRILL_DAY]) == 1
    assert main(["compare", str(sheet), str(data)]) == 2
    assert "--date" in capsys.readouterr().err


def test_ongoing_visit_unreadable_time_and_empty_sheet():
    records = [*drill_records(), {"timestamp": "壞掉", "topic": "radar10/FIELD-01", "resolution": None, "payload": {}}]
    # 下一項 10:03:30 開始，所以一般使用這一段只算到 10:02:30；那時人還在裡面，照樣算有人使用
    steps = read_sheet("項目,狀況,手錶時間,備註\n1,一般使用,10:01:00,\n9,結束對時,10:03:30,\n", datetime(2026, 10, 19).date())
    assert [c.status for c in compare(steps, records).checks] == ["符合", "符合"]
    empty = comparison_text(compare(read_sheet(sheet_text(), None), records))
    assert "時間差中位數" not in empty and "沒有操演 10" in empty
