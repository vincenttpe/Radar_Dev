"""2026-10-08 取得的兩種新樣本：固定目前看到的欄位與值，格式一變就會知道。

- data/samples_fall/：動作描述 JSON，多 `fall` 欄位
- data/samples_device_event/：雲端從裝置收到的簡易動作事件
"""
import json
import re

TIMESTAMP = re.compile(r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2} [AP]M$")


def test_samples_fall_fields_and_values(repo):
    files = sorted((repo / "data" / "samples_fall").glob("*.json"))
    assert [p.name for p in files] == ["2026-06-03.json"]
    records = json.loads(files[0].read_text(encoding="utf-8"))
    assert len(records) == 11
    for rec in records:
        assert set(rec) == {"Timestamp", "Motion", "Speed", "fall"}, rec
        assert TIMESTAMP.match(rec["Timestamp"]), rec["Timestamp"]
        assert rec["Speed"] in {"Slow", "Normal", "Fast"}, rec
        assert rec["fall"] in {"yes", "no"}, rec
        assert rec["Motion"].strip() and rec["Motion"].isascii(), rec


def test_samples_fall_has_exactly_one_fall(repo):
    records = json.loads((repo / "data" / "samples_fall" / "2026-06-03.json").read_text(encoding="utf-8"))
    falls = [r for r in records if r["fall"] == "yes"]
    assert len(falls) == 1
    assert falls[0]["Motion"] == "A person slumped from the chair onto the floor."


def test_device_event_fields(repo):
    files = sorted((repo / "data" / "samples_device_event").glob("*.json"))
    assert [p.name for p in files] == ["absence.json"]
    event = json.loads(files[0].read_text(encoding="utf-8"))
    assert event == {"mode": "radar", "event": "absence", "trigger": "track", "userReply": False}
    assert isinstance(event["userReply"], bool)
