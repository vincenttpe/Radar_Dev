"""事件格式草案 schemas/event.v0.draft.json 的行為。

草案確認後改名為 event.v1.json 時，這裡的路徑與 schema_version 要一起改。
"""
import pytest
from jsonschema import Draft202012Validator


@pytest.fixture(scope="module")
def validator(event_schema):
    Draft202012Validator.check_schema(event_schema)
    return Draft202012Validator(event_schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def errors(validator, event) -> list[str]:
    return sorted(e.message for e in validator.iter_errors(event))


BASE = {"schema_version": "0-draft", "event_id": "evt-001", "device_id": "radar-01"}
T = "2026-10-08T10:00:00+08:00"


def test_fall_event_needs_only_detected(validator):
    event = {**BASE, "event_type": "fall", "timestamps": {"detected": T}}
    assert errors(validator, event) == []


def test_heartbeat_event_needs_only_detected(validator):
    event = {**BASE, "event_type": "heartbeat", "timestamps": {"detected": T}}
    assert errors(validator, event) == []


@pytest.mark.parametrize("event_type", ["posture", "activity"])
def test_posture_and_activity_require_started_and_ended(validator, event_type):
    missing = {**BASE, "event_type": event_type, "timestamps": {"detected": T}}
    assert errors(validator, missing) == [
        "'ended' is a required property",
        "'started' is a required property",
    ]
    complete = {**BASE, "event_type": event_type, "timestamps": {"detected": T, "started": T, "ended": T}}
    assert errors(validator, complete) == []


def test_missing_event_type_reports_only_event_type(validator):
    """if/then 的條件有 required，event_type 缺席時不該連帶要求 started/ended。"""
    event = {**BASE, "timestamps": {"detected": T}}
    assert errors(validator, event) == ["'event_type' is a required property"]


def test_unknown_top_level_field_rejected(validator):
    event = {**BASE, "event_type": "fall", "timestamps": {"detected": T}, "foo": 1}
    assert errors(validator, event) == ["Additional properties are not allowed ('foo' was unexpected)"]


def test_unknown_timestamp_field_rejected(validator):
    event = {**BASE, "event_type": "fall", "timestamps": {"detected": T, "duration": 3}}
    assert errors(validator, event) == ["Additional properties are not allowed ('duration' was unexpected)"]


def test_voice_confirmation_result_enum(validator):
    ok = {**BASE, "event_type": "fall", "timestamps": {"detected": T},
          "voice_confirmation": {"result": "no_response", "language": "nan-TW"}}
    assert errors(validator, ok) == []
    bad = {**BASE, "event_type": "fall", "timestamps": {"detected": T},
           "voice_confirmation": {"result": "maybe"}}
    assert errors(validator, bad) == ["'maybe' is not one of ['confirmed_fall', 'denied', 'no_response', 'not_asked']"]


def test_measurement_requires_name_value_unit(validator):
    event = {**BASE, "event_type": "activity",
             "timestamps": {"detected": T, "started": T, "ended": T},
             "measurements": [{"name": "walking_speed", "value": 0.8}]}
    assert errors(validator, event) == ["'unit' is a required property"]


def test_confidence_bounds(validator):
    event = {**BASE, "event_type": "fall", "timestamps": {"detected": T}, "confidence": 1.5}
    assert errors(validator, event) == ["1.5 is greater than the maximum of 1"]


def test_schema_version_is_fixed(validator):
    event = {**BASE, "schema_version": "1", "event_type": "fall", "timestamps": {"detected": T}}
    assert errors(validator, event) == ["'0-draft' was expected"]


def test_format_checker_is_really_active():
    """沒有 rfc3339-validator 時 date-time 不會被檢查；這個測試防止依賴被默默拿掉。"""
    checker = Draft202012Validator.FORMAT_CHECKER
    assert not checker.conforms("not-a-date", "date-time")
    assert checker.conforms("2026-10-08T10:00:00+08:00", "date-time")


def test_invalid_date_time_rejected(validator):
    event = {**BASE, "event_type": "fall", "timestamps": {"detected": "not-a-date"}}
    assert errors(validator, event) == ["'not-a-date' is not a 'date-time'"]


def test_date_time_without_timezone_rejected(validator):
    """schema 說明要求含時區；RFC 3339 的 date-time 也要求時區。"""
    event = {**BASE, "event_type": "fall", "timestamps": {"detected": "2026-10-08T10:00:00"}}
    assert errors(validator, event) == ["'2026-10-08T10:00:00' is not a 'date-time'"]


@pytest.mark.parametrize("field", ["event_id", "device_id", "subject_id", "label"])
def test_empty_identifier_strings_rejected(validator, field):
    event = {**BASE, "event_type": "fall", "timestamps": {"detected": T}, field: ""}
    assert errors(validator, event) == ["'' should be non-empty"]
