"""kg/v1/thresholds.json：結構化門檻與等級名稱一致，並固定目前已知的缺口與重疊。

thresholds.json 是 radar-kg-build 從 graph.cypher 的等級名稱轉成數字，沒有修正任何門檻。
來源（graph.cypher、graph.docx、standard_suggestion.xlsx）本身就有缺口與重疊，
修正需要審核人決定（docs/05_會議待詢問清單.md E5）。

這裡的做法和 test_label_data.py 一樣：把「目前已知的問題」寫成清單，測試要求
實際找到的問題與清單完全相同。新增問題會失敗；問題被修掉也會失敗，提醒要一起
更新清單與文件。
"""
import json
from decimal import Decimal
from itertools import pairwise

import pytest

from radar_agent import kg_rules
from radar_agent.kg_source import find_threshold_issues, parse_level_range

# (指標, 低等級分數, 高等級分數, 'gap'|'overlap')；邊界不在精度格點上是 (指標, 分數, None, 'off_grid')。名詞定義見 CONTEXT.md。
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
    names = {(m["name"], lv["score"]): lv["name"] for m in metrics_json["metrics"] for lv in m["levels"]}
    seen = set()
    for m in thresholds["metrics"]:
        for lv in m["levels"]:
            key = (m["name"], lv["score"])
            seen.add(key)
            assert lv["source_text"] == names[key], key
            lo, hi = parse_level_range(names[key])
            assert (lv["min"], lv["max"]) == (lo, hi), key
            assert lv["min_inclusive"] is True
            assert lv["max_inclusive"] is (hi is not None)
    assert seen == set(names)


def test_each_metric_has_exactly_one_open_ended_level_and_starts_at_zero(thresholds):
    """每個指標有一個「以上」的開放上界等級，並且最小的 min 是 0。

    來回行走是時間越短越好，開放上界的是 0 分；其他指標是 4 分（步行速度是 3 分）。
    """
    for m in thresholds["metrics"]:
        assert sum(1 for lv in m["levels"] if lv["max"] is None) == 1, m["name"]
        assert min(lv["min"] for lv in m["levels"]) == 0, m["name"]


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


# ── 建置檢查與執行期判定要一致 ───────────────────────────────

def _off_grid_metric(low_max, high_min):
    """精度 0.1，但邊界寫到小數兩位（不在精度格點上）。"""
    def lv(score, lo, hi):
        return {"score": score, "rank": str(score), "min": lo, "max": hi,
                "min_inclusive": True, "max_inclusive": hi is not None, "source_text": ""}
    return {"name": "x", "unit": "秒", "resolution": 0.1, "levels": [lv(0, 0, low_max), lv(1, high_min, None)]}


def _assess(metric, value):
    kg = kg_rules.KnowledgeGraph(
        info={"version": "t", "approval": {}, "approved": False},
        thresholds={metric["name"]: metric},
        suggestions={(metric["name"], lv["score"]): {"suggestion": "", "recommended_dose": ""} for lv in metric["levels"]},
    )
    return kg_rules.assess(kg, metric["name"], value)


def test_gap_between_off_grid_boundaries_is_found_like_at_runtime():
    """5.45 與 5.55 之間的 5.5 不屬於任何等級：建置檢查與執行期都說是缺口，兩個邊界也都不在格點上。"""
    metric = _off_grid_metric(5.45, 5.55)
    assert find_threshold_issues(metric) == {("x", 0, 1, "gap"), ("x", 0, None, "off_grid"), ("x", 1, None, "off_grid")}
    a = _assess(metric, "5.5")
    assert a["status"] == "unassessable" and "不屬於任何等級" in a["reason"]


def test_shared_off_grid_boundary_is_not_an_overlap():
    """兩級都含 5.45，但 5.45 不在格點上，執行期先以精度拒絕，不會遇到重疊；建置檢查只回報邊界不在格點上。"""
    metric = _off_grid_metric(5.45, 5.45)
    assert find_threshold_issues(metric) == {("x", 0, None, "off_grid"), ("x", 1, None, "off_grid")}
    a = _assess(metric, "5.45")
    assert a["status"] == "unassessable" and "精度" in a["reason"] and a["candidates"] == []


def _issues_by_assessing_every_grid_point(metric):
    """沿精度格點逐一呼叫 assess，把「不屬於任何等級」與「同時屬於兩級」換算成 find_threshold_issues 的格式。"""
    levels = sorted(metric["levels"], key=lambda lv: Decimal(str(lv["min"])))
    edges = [Decimal(str(e)) for lv in levels for e in (lv["min"], lv["max"]) if e is not None]
    step = Decimal(str(metric["resolution"]))
    issues = set()
    for value in kg_rules.grid_points(metric, min(edges), max(edges) + step):
        a = _assess(metric, str(value))
        if a["status"] == "assessed":
            continue
        if a["candidates"]:
            pair = sorted((lv for lv in levels if lv["score"] in {c["score"] for c in a["candidates"]}),
                          key=lambda lv: Decimal(str(lv["min"])))
            issues |= {(metric["name"], lo["score"], hi["score"], "overlap") for lo, hi in pairwise(pair)}
        else:
            assert "不屬於任何等級" in a["reason"], (value, a["reason"])
            below = max((lv for lv in levels if lv["max"] is not None and Decimal(str(lv["max"])) <= value),
                        key=lambda lv: Decimal(str(lv["max"])))
            above = min((lv for lv in levels if Decimal(str(lv["min"])) >= value), key=lambda lv: Decimal(str(lv["min"])))
            issues.add((metric["name"], below["score"], above["score"], "gap"))
    return issues


def _without_off_grid(issues):
    return {i for i in issues if i[3] != "off_grid"}


def test_build_check_agrees_with_assessing_every_grid_point_on_v1(thresholds):
    """建置時說沒有缺口或重疊，執行期就不會遇到；反之亦然。步行速度是連續量，沒有格點可掃。"""
    discrete = [m for m in thresholds["metrics"] if m["resolution"] is not None]
    assert len(discrete) == 4
    for m in discrete:
        assert _issues_by_assessing_every_grid_point(m) == find_threshold_issues(m), m["name"]


@pytest.mark.parametrize("low_max,high_min", [(5.45, 5.55), (5.45, 5.45), (5.4, 5.6), (5.4, 5.5), (5.5, 5.4)])
def test_build_check_agrees_with_assessing_every_grid_point_on_synthetic_metrics(low_max, high_min):
    metric = _off_grid_metric(low_max, high_min)
    assert _issues_by_assessing_every_grid_point(metric) == _without_off_grid(find_threshold_issues(metric))
