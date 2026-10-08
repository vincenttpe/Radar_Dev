"""舊雛形的測試輸入 data/samples/*.json。

這是目前唯一看得到的「雷達資訊 JSON」格式，新系統的 7 天資料欄位確認前
（待確認事項第 1 項）先以它為草稿依據。
"""
import json
import re

EXPECTED_FIELDS = {"Timestamp", "Motion", "Speed"}
SPEEDS = {"Slow", "Normal", "Fast"}
TIMESTAMP = re.compile(r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}(?: [AP]M)?$")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_six_files_with_64_records(sample_files):
    assert [p.name for p in sample_files] == ["01.json", "02.json", "03.json", "04.json", "05.json", "move.json"]
    assert sum(len(load(p)) for p in sample_files) == 64


def test_every_record_has_exactly_the_three_fields(sample_files):
    for path in sample_files:
        records = load(path)
        assert isinstance(records, list), path.name
        for rec in records:
            assert set(rec) == EXPECTED_FIELDS, (path.name, rec)


def test_speed_values(sample_files):
    speeds = {rec["Speed"] for p in sample_files for rec in load(p)}
    assert speeds == SPEEDS


def test_timestamp_format(sample_files):
    for path in sample_files:
        for rec in load(path):
            assert TIMESTAMP.match(rec["Timestamp"]), (path.name, rec["Timestamp"])


def test_motion_is_non_empty_english_sentence(sample_files):
    for path in sample_files:
        for rec in load(path):
            motion = rec["Motion"]
            assert motion.strip(), (path.name, rec)
            assert motion.isascii(), (path.name, motion)
