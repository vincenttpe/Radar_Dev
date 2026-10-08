"""radar_agent.event_validation.validate_event：Schema 加時間順序規則。"""
import pytest

from radar_agent.event_validation import validate_event

BASE = {"schema_version": "0-draft", "event_id": "evt-001", "device_id": "radar-01"}
T0 = "2026-10-08T10:00:00+08:00"
T1 = "2026-10-08T10:01:00+08:00"
T2 = "2026-10-08T10:05:00+08:00"


def activity(**stamps):
    return {**BASE, "event_type": "activity", "timestamps": {"detected": T0, **stamps}}


def test_valid_fall_passes():
    assert validate_event({**BASE, "event_type": "fall", "timestamps": {"detected": T0}}) == []


def test_valid_activity_passes():
    assert validate_event(activity(started=T1, ended=T2)) == []


def test_ended_equal_to_started_is_allowed():
    assert validate_event(activity(started=T1, ended=T1)) == []


def test_ended_before_started_rejected():
    assert validate_event(activity(started=T2, ended=T1)) == ["timestamps：ended 不得早於 started"]


def test_order_compares_instants_not_strings():
    """比的是時間點，不是字串：字串上 10:30 比 03:00 大，換算成 UTC 後 02:30 卻比 03:00 早。"""
    started = "2026-10-08T03:00:00+00:00"
    ended = "2026-10-08T10:30:00+08:00"  # = 02:30 UTC，早於 started
    assert validate_event(activity(started=started, ended=ended)) == ["timestamps：ended 不得早於 started"]


def test_voice_answered_before_asked_rejected():
    event = {**BASE, "event_type": "fall",
             "timestamps": {"detected": T0, "voice_asked": T2, "voice_answered": T1}}
    assert validate_event(event) == ["timestamps：voice_answered 不得早於 voice_asked"]


def test_schema_error_is_reported_with_path_and_order_not_checked():
    event = activity(started="not-a-date", ended=T1)
    errors = validate_event(event)
    assert errors == ["timestamps.started：'not-a-date' is not a 'date-time'"]


def test_missing_started_ended_for_activity():
    assert validate_event(activity()) == [
        "timestamps：'ended' is a required property",
        "timestamps：'started' is a required property",
    ]


def test_empty_event_id_rejected():
    event = {**BASE, "event_id": "", "event_type": "fall", "timestamps": {"detected": T0}}
    assert validate_event(event) == ["event_id：'' should be non-empty"]


def test_never_raises_on_garbage():
    for garbage in [{}, {"timestamps": 3}, {**BASE, "event_type": "fall", "timestamps": {"detected": 5}}]:
        assert isinstance(validate_event(garbage), list)
        assert validate_event(garbage) != []



@pytest.mark.parametrize("started,ended", [
    ("2026-10-08t10:00:00z", "2026-10-08T10:05:00Z"),   # RFC 3339 允許小寫 t／z
    ("2026-10-08T10:00:00z", "2026-10-08t10:05:00+08:00"),
])
def test_lowercase_t_and_z_are_accepted_and_compared(started, ended):
    """評估重現的情況：小寫通過 Schema，卻讓 datetime.fromisoformat 拋例外。"""
    assert validate_event(activity(started=started, ended=ended)) in ([], ["timestamps：ended 不得早於 started"])


def test_lowercase_order_is_still_checked():
    assert validate_event(activity(started="2026-10-08t10:05:00z", ended="2026-10-08t10:00:00z")) == [
        "timestamps：ended 不得早於 started"
    ]


@pytest.mark.parametrize("stamp", [
    "2026-12-31T23:59:60Z",     # 閏秒：RFC 3339 允許，datetime 不支援
    "2026-10-08t10:00:00.123456789z",
])
def test_unusual_but_valid_rfc3339_never_raises(stamp):
    errors = validate_event(activity(started=stamp, ended="2027-01-01T00:00:00Z"))
    assert isinstance(errors, list)


def test_parse_failure_becomes_a_message_not_an_exception(monkeypatch):
    from radar_agent import event_validation

    def boom(text):
        raise ValueError("解析失敗")

    monkeypatch.setattr(event_validation, "parse_instant", boom)
    assert validate_event(activity(started=T1, ended=T2)) == ["timestamps：無法比較 started 與 ended 的先後（解析失敗）"]
