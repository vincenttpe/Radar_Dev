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
