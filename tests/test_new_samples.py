"""2026-10-08 取得的兩種新樣本：固定目前看到的欄位與值，格式一變就會知道。

- data/samples_fall/：動作描述 JSON，多 `fall` 欄位
- data/samples_device_event/：雲端從裝置收到的簡易動作事件，以及 2026-10-12 的 MQTT 訊息規格
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
    assert [p.name for p in files] == ["absence.json", "mqtt_messages_2026-10-12.json"]
    event = json.loads((repo / "data" / "samples_device_event" / "absence.json").read_text(encoding="utf-8"))
    assert event == {"mode": "radar", "event": "absence", "trigger": "track", "userReply": False}
    assert isinstance(event["userReply"], bool)


def _mqtt_messages(repo):
    spec = json.loads(
        (repo / "data" / "samples_device_event" / "mqtt_messages_2026-10-12.json").read_text(encoding="utf-8")
    )
    return spec, spec["messages"]


def test_mqtt_spec_enumerations(repo):
    """固定規格裡出現的列舉值；裝置端改版時這裡會先紅。"""
    spec, messages = _mqtt_messages(repo)
    assert spec["topic"] == "radar10/{{identifier}}"
    assert len(messages) == 13
    assert len({m["id"] for m in messages}) == 13
    payloads = [m["payload"] for m in messages]
    assert {p["event"] for p in payloads} == {
        "online", "offline", "presence", "absence", "moving", "static", "longstay", "fall", "resolved",
    }
    assert {p["trigger"] for p in payloads if "trigger" in p} == {
        "track", "timeout", "conversation", "standUp", "noNeedHelp",
    }
    assert {p["mode"] for p in payloads if "mode" in p} == {"radar", "speech", "fusion"}
    for p in payloads:
        if "userReply" in p:
            assert isinstance(p["userReply"], bool), p
        if p["event"] in {"online", "offline"}:
            assert p == {"event": p["event"]}
        else:
            assert {"event", "trigger", "mode", "userReply"} <= set(p), p
        # delaysec 只跟著 timeout 出現
        assert ("delaysec" in p) == (p.get("trigger") == "timeout"), p


def test_mqtt_spec_fall_alarm_variants(repo):
    """跌倒警報四種、解除兩種；mode=speech 的 fall 映射為 DistressCall（主動呼救，不一定是跌倒）。"""
    _, messages = _mqtt_messages(repo)
    falls = {(m["payload"]["trigger"], m["payload"]["mode"], m["payload"]["userReply"]) for m in messages
             if m["payload"]["event"] == "fall"}
    assert falls == {
        ("timeout", "radar", False), ("conversation", "fusion", True),
        ("conversation", "speech", True), ("timeout", "speech", False),
    }
    for m in messages:
        p = m["payload"]
        is_distress = p["event"] == "fall" and p.get("mode") == "speech"
        assert (m.get("maps_to") == "DistressCall") == is_distress, m
        if p.get("trigger") == "timeout" and p["event"] == "fall":
            assert p["delaysec"] == 10  # 與待確認事項第 4 項「等 10 秒」一致
    resolved = {(m["payload"]["trigger"], m["payload"]["mode"]) for m in messages
                if m["payload"]["event"] == "resolved"}
    assert resolved == {("standUp", "radar"), ("noNeedHelp", "speech")}


def test_absence_sample_matches_mqtt_spec(repo):
    """2026-10-08 實際收到的那一筆，與規格的「無人」一致。"""
    _, messages = _mqtt_messages(repo)
    sample = json.loads((repo / "data" / "samples_device_event" / "absence.json").read_text(encoding="utf-8"))
    spec_absence = next(m["payload"] for m in messages if m["id"] == "absence")
    assert sample == spec_absence
