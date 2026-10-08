"""知識圖譜 v1：graph.cypher 是正本，metrics.json 與 patch_wording.cypher 都要和它一致。"""
from radar_agent import kg_source

EXPECTED_METRICS = {"下肢肌力", "單腳站立", "來回行走", "心肺耐力", "步行速度"}
EXPECTED_SCORES = {
    "下肢肌力": {0, 1, 2, 3, 4},
    "單腳站立": {0, 1, 2, 3, 4},
    "來回行走": {0, 1, 2, 3, 4},
    "心肺耐力": {0, 1, 2, 3, 4},
    "步行速度": {0, 1, 2, 3},  # 沒有 4 分等級，見 kg/README.md
}
RANK_BY_SCORE = {4: "很好", 3: "尚好", 2: "普通", 1: "稍差", 0: "不好"}


def test_cypher_has_five_metrics_with_expected_levels(kg_levels_from_cypher):
    by_metric = {}
    for (metric, score) in kg_levels_from_cypher:
        by_metric.setdefault(metric, set()).add(score)
    assert set(by_metric) == EXPECTED_METRICS
    assert by_metric == EXPECTED_SCORES
    assert len(kg_levels_from_cypher) == 24


def test_rank_matches_score(kg_levels_from_cypher):
    for (metric, score), level in kg_levels_from_cypher.items():
        assert level["rank"] == RANK_BY_SCORE[score], (metric, score, level["rank"])


def test_every_level_has_suggestion_and_dose(kg_levels_from_cypher):
    for key, level in kg_levels_from_cypher.items():
        assert level["suggestion"], key
        assert level["recommended_dose"], key


def test_metrics_json_matches_cypher(kg_levels_from_cypher, metrics_json):
    from_json = {
        (m["name"], lvl["score"]): {
            "name": lvl["name"],
            "rank": lvl["rank"],
            "suggestion": lvl["suggestion"],
            "recommended_dose": lvl.get("recommended_dose"),
        }
        for m in metrics_json["metrics"]
        for lvl in m["levels"]
    }
    assert from_json == kg_levels_from_cypher


def test_wording_matches_rank_for_metrics_that_name_themselves(kg_levels_from_cypher):
    """下肢肌力、心肺耐力的建議以「<指標><程度>」開頭：4 分要寫「很好」、3 分寫「良好」。

    v1 原始文件這兩個是顛倒的，2026-10-08 已修正；此測試防止改回去。
    """
    for metric in ("下肢肌力", "心肺耐力"):
        assert kg_levels_from_cypher[(metric, 4)]["suggestion"].startswith(f"{metric}很好")
        assert kg_levels_from_cypher[(metric, 3)]["suggestion"].startswith(f"{metric}良好")


def test_patch_wording_matches_cypher(repo, graph_cypher):
    """patch_wording.cypher 寫進 Neo4j 的 content 必須和 graph.cypher 的對應節點逐字相同。"""
    patch = (repo / "kg" / "v1" / "patch_wording.cypher").read_text(encoding="utf-8")
    statements = kg_source.parse_patch(patch)
    assert len(statements) == 4
    raw = kg_source.raw_suggestions(graph_cypher)
    for metric, score, content in statements:
        assert content == raw[(metric, score)], (metric, score)
