"""kg/v1/thresholds.json：結構化門檻與等級名稱一致，並固定目前已知的缺口與重疊。

thresholds.json 是 radar-kg-build 從 graph.cypher 的等級名稱轉成數字，沒有修正任何門檻。
來源（graph.cypher、graph.docx、standard_suggestion.xlsx）本身就有缺口與重疊，
修正需要審核人決定（docs/05_會議待詢問清單.md E5）。

這裡的做法和 test_label_data.py 一樣：把「目前已知的問題」寫成清單，測試要求
實際找到的問題與清單完全相同。新增問題會失敗；問題被修掉也會失敗，提醒要一起
更新清單與文件。
"""
import json

import pytest

from radar_agent.kg_source import find_threshold_issues, parse_level_range

# (指標, 低等級分數, 高等級分數, 種類)
KNOWN_ISSUES = {
    # 3 分到 89~99、4 分從 112 起，次數是整數，100~111 沒有等級。
    ("心肺耐力", 3, 4, "gap"),
    # 相鄰兩級共用邊界且兩邊都含，0.5、0.8、1.0 各自同時屬於兩個等級。
    ("步行速度", 0, 1, "overlap"),
    ("步行速度", 1, 2, "overlap"),
    ("步行速度", 2, 3, "overlap"),
}


@pytest.fixture(scope="module")
def thresholds(repo) -> dict:
    return json.loads((repo / "kg" / "v1" / "thresholds.json").read_text(encoding="utf-8"))


def test_five_metrics_and_24_levels(thresholds):
    assert [m["name"] for m in thresholds["metrics"]] == ["下肢肌力", "單腳站立", "來回行走", "心肺耐力", "步行速度"]
    assert sum(len(m["levels"]) for m in thresholds["metrics"]) == 24


def test_thresholds_match_level_names_in_metrics_json(thresholds, metrics_json):
    names = {(m["name"], l["score"]): l["name"] for m in metrics_json["metrics"] for l in m["levels"]}
    seen = set()
    for m in thresholds["metrics"]:
        for l in m["levels"]:
            key = (m["name"], l["score"])
            seen.add(key)
            assert l["source_text"] == names[key], key
            lo, hi = parse_level_range(names[key])
            assert (l["min"], l["max"]) == (lo, hi), key
            assert l["min_inclusive"] is True
            assert l["max_inclusive"] is (hi is not None)
    assert seen == set(names)


def test_each_metric_has_exactly_one_open_ended_level_and_starts_at_zero(thresholds):
    """每個指標有一個「以上」的開放上界等級，並且最小的 min 是 0。

    來回行走是時間越短越好，開放上界的是 0 分；其他指標是 4 分（步行速度是 3 分）。
    """
    for m in thresholds["metrics"]:
        assert sum(1 for l in m["levels"] if l["max"] is None) == 1, m["name"]
        assert min(l["min"] for l in m["levels"]) == 0, m["name"]


def test_known_gaps_and_overlaps_are_exactly_the_documented_ones(thresholds):
    found = set()
    for m in thresholds["metrics"]:
        found |= find_threshold_issues(m)
    assert found == KNOWN_ISSUES


def test_resolution_is_stated_or_explained(thresholds):
    """沒有數字精度的指標必須寫明原因；有精度的要寫來源依據。"""
    for m in thresholds["metrics"]:
        assert m["resolution_note"], m["name"]
        assert (m["resolution"] is None) or (m["resolution"] > 0), m["name"]


def test_find_issues_detects_synthetic_gap_and_overlap():
    """檢查器本身要能抓到問題，避免 KNOWN_ISSUES 靠它誤判。"""
    def lv(score, lo, hi, hi_incl=True):
        return {"score": score, "min": lo, "max": hi, "min_inclusive": True, "max_inclusive": hi_incl}

    clean = {"name": "x", "resolution": 1, "levels": [lv(0, 0, 4), lv(1, 5, None, False)]}
    assert find_threshold_issues(clean) == set()
    gap = {"name": "x", "resolution": 1, "levels": [lv(0, 0, 4), lv(1, 6, None, False)]}
    assert find_threshold_issues(gap) == {("x", 0, 1, "gap")}
    overlap = {"name": "x", "resolution": 1, "levels": [lv(0, 0, 5), lv(1, 5, None, False)]}
    assert find_threshold_issues(overlap) == {("x", 0, 1, "overlap")}
    continuous_touching = {"name": "x", "resolution": None, "levels": [lv(0, 0, 5, False), lv(1, 5, None, False)]}
    assert find_threshold_issues(continuous_touching) == set()


def test_open_ended_level_that_is_not_the_highest_is_an_error():
    open_low = {"name": "x", "resolution": 1, "levels": [
        {"score": 0, "min": 0, "max": None, "min_inclusive": True, "max_inclusive": False},
        {"score": 1, "min": 5, "max": 9, "min_inclusive": True, "max_inclusive": True},
    ]}
    with pytest.raises(ValueError, match="開放上界"):
        find_threshold_issues(open_low)


@pytest.mark.parametrize("name,expected", [
    ("起站30秒 14~17次", (14, 17)),
    ("起站30秒 18次以上", (18, None)),
    ("2.44公尺行走 5.5~6.1秒", (5.5, 6.1)),
    ("2.44公尺行走 7.6秒以上", (7.6, None)),
    ("步行速度 0.8~1.0 m/s", (0.8, 1)),
    ("步行速度 1.0 m/s以上", (1, None)),
])
def test_parse_level_range(name, expected):
    assert parse_level_range(name) == expected


def test_parse_level_range_rejects_unparseable_name():
    with pytest.raises(ValueError, match="無法從等級名稱解析門檻"):
        parse_level_range("沒有數字的名稱")


def _lv(score, lo, hi, lo_incl=True, hi_incl=True):
    return {"score": score, "min": lo, "max": hi, "min_inclusive": lo_incl, "max_inclusive": hi_incl}


@pytest.mark.parametrize("name,resolution,low,high,expected", [
    # 範圍交疊（不只是邊界相等）
    ("交疊", 1, _lv(0, 0, 6), _lv(1, 5, None, hi_incl=False), "overlap"),
    ("交疊，連續量", None, _lv(0, 0, 6.5), _lv(1, 5.5, None, hi_incl=False), "overlap"),
    # 邊界相等
    ("邊界相等且兩邊都含", None, _lv(0, 0, 5), _lv(1, 5, None, hi_incl=False), "overlap"),
    ("邊界相等，上界不含", None, _lv(0, 0, 5, hi_incl=False), _lv(1, 5, None, hi_incl=False), None),
    ("邊界相等，下界不含", None, _lv(0, 0, 5), _lv(1, 5, None, lo_incl=False, hi_incl=False), None),
    ("邊界相等，兩邊都不含", None, _lv(0, 0, 5, hi_incl=False), _lv(1, 5, None, lo_incl=False, hi_incl=False), "gap"),
    # 連續量的縫
    ("連續量有縫", None, _lv(0, 0, 5), _lv(1, 6, None, hi_incl=False), "gap"),
    # 離散量
    ("整數剛好相接", 1, _lv(0, 0, 4), _lv(1, 5, None, hi_incl=False), None),
    ("整數差一個值", 1, _lv(0, 0, 4), _lv(1, 6, None, hi_incl=False), "gap"),
    ("整數，上界不含而下一級從上界起", 1, _lv(0, 0, 5, hi_incl=False), _lv(1, 5, None, hi_incl=False), None),
    ("整數，上界不含，下一級從上界加一起", 1, _lv(0, 0, 5, hi_incl=False), _lv(1, 6, None, hi_incl=False), "gap"),
    ("小數一位剛好相接", 0.1, _lv(0, 0, 5.4), _lv(1, 5.5, None, hi_incl=False), None),
    ("小數一位差一格", 0.1, _lv(0, 0, 5.4), _lv(1, 5.6, None, hi_incl=False), "gap"),
])
def test_find_threshold_issues_cases(name, resolution, low, high, expected):
    metric = {"name": "x", "resolution": resolution, "levels": [low, high]}
    assert find_threshold_issues(metric) == (set() if expected is None else {("x", 0, 1, expected)}), name
