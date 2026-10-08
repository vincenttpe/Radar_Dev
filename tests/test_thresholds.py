"""kg/v1/thresholds.json：結構化門檻與等級名稱一致，並固定目前已知的缺口與重疊。

thresholds.json 只是把 metrics.json 的等級名稱轉成數字，沒有修正任何門檻。
來源（graph.cypher、graph.docx、standard_suggestion.xlsx）本身就有缺口與重疊，
修正需要審核人決定（docs/05_會議待詢問清單.md E5）。

這裡的做法和 test_label_data.py 一樣：把「目前已知的問題」寫成清單，測試要求
實際找到的問題與清單完全相同。新增問題會失敗；問題被修掉也會失敗，提醒要一起
更新清單與文件。
"""
import json
import re
from decimal import Decimal

import pytest

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


def D(x):
    return None if x is None else Decimal(str(x))


def parse_name(name: str):
    m = re.search(r"(\d+(?:\.\d+)?)\s*~\s*(\d+(?:\.\d+)?)", name)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:秒|次|m/s)?\s*以上", name)
    if m:
        return float(m.group(1)), None
    raise AssertionError(f"無法解析等級名稱：{name}")


def find_issues(metric: dict) -> set[tuple]:
    """依各指標標示的 resolution 檢查相鄰等級有沒有缺口或重疊。

    把等級依 min 排序，逐對比較「較低那級的上界」與「較高那級的下界」。
    resolution 是量測值的最小間距（整數次數為 1）；None 代表連續量。
    """
    res = D(metric["resolution"])
    levels = sorted(metric["levels"], key=lambda l: D(l["min"]))
    issues = set()
    for low, high in zip(levels, levels[1:]):
        edge_low, edge_high = D(low["max"]), D(high["min"])
        assert edge_low is not None, f'{metric["name"]} 分數 {low["score"]} 是開放上界，卻不是最高那一級'
        key = (metric["name"], low["score"], high["score"])

        if edge_low > edge_high or (edge_low == edge_high and low["max_inclusive"] and high["min_inclusive"]):
            issues.add(key + ("overlap",))
            continue

        if res is None:  # 連續量：邊界相等且至少一邊含才相接
            touching = edge_low == edge_high and (low["max_inclusive"] or high["min_inclusive"])
            if not touching:
                issues.add(key + ("gap",))
            continue

        # 離散量：低等級之後第一個沒被涵蓋的值，必須被高等級涵蓋
        first_uncovered = edge_low + res if low["max_inclusive"] else edge_low
        first_covered_by_high = edge_high if high["min_inclusive"] else edge_high + res
        if first_uncovered < first_covered_by_high:
            issues.add(key + ("gap",))
    return issues


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
            lo, hi = parse_name(names[key])
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
        found |= find_issues(m)
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
    assert find_issues(clean) == set()
    gap = {"name": "x", "resolution": 1, "levels": [lv(0, 0, 4), lv(1, 6, None, False)]}
    assert find_issues(gap) == {("x", 0, 1, "gap")}
    overlap = {"name": "x", "resolution": 1, "levels": [lv(0, 0, 5), lv(1, 5, None, False)]}
    assert find_issues(overlap) == {("x", 0, 1, "overlap")}
    continuous_touching = {"name": "x", "resolution": None, "levels": [lv(0, 0, 5, False), lv(1, 5, None, False)]}
    assert find_issues(continuous_touching) == set()
