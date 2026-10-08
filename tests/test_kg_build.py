"""radar-kg-build：metrics.json、thresholds.json 由 graph.cypher 產生，且產生結果和 repo 裡的一致。"""
import json
import shutil

import pytest

from radar_agent import kg_source


@pytest.fixture
def tmp_repo(repo, tmp_path):
    """只有 graph.cypher 的小 repo；JSON 還不存在。"""
    (tmp_path / "kg" / "v1").mkdir(parents=True)
    shutil.copy(repo / kg_source.CYPHER, tmp_path / kg_source.CYPHER)
    return tmp_path


def test_repo_json_files_are_exactly_what_the_generator_produces(repo):
    """逐字比對：有人手改了 JSON，或改了 graph.cypher 卻沒重新產生，這裡會失敗。"""
    for path, expected in kg_source.build_all(repo).items():
        assert (repo / path).read_text(encoding="utf-8") == expected, f"{path} 與 graph.cypher 不一致，請執行 radar-kg-build"


def test_check_mode_on_the_real_repo(capsys):
    assert kg_source.main(["--check"]) == 0
    assert "一致" in capsys.readouterr().out


def test_write_mode_creates_missing_files(tmp_repo, capsys):
    assert kg_source.main([], repo=tmp_repo) == 0
    for path, expected in kg_source.build_all(tmp_repo).items():
        assert (tmp_repo / path).read_text(encoding="utf-8") == expected
    assert "已更新" in capsys.readouterr().out


def test_write_mode_is_idempotent(tmp_repo, capsys):
    kg_source.main([], repo=tmp_repo)
    before = {p: (tmp_repo / p).read_bytes() for p in (kg_source.METRICS_JSON, kg_source.THRESHOLDS_JSON)}
    capsys.readouterr()
    assert kg_source.main([], repo=tmp_repo) == 0
    assert {p: (tmp_repo / p).read_bytes() for p in before} == before
    assert "沒有變動" in capsys.readouterr().out


def test_check_detects_a_hand_edited_json_and_write_repairs_it(tmp_repo, capsys):
    kg_source.main([], repo=tmp_repo)
    metrics_path = tmp_repo / kg_source.METRICS_JSON
    data = json.loads(metrics_path.read_text(encoding="utf-8"))
    data["metrics"][0]["levels"][0]["suggestion"] = "有人手改了這裡"
    metrics_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    capsys.readouterr()
    assert kg_source.main(["--check"], repo=tmp_repo) == 1
    assert kg_source.METRICS_JSON in capsys.readouterr().out
    assert kg_source.main([], repo=tmp_repo) == 0
    assert kg_source.main(["--check"], repo=tmp_repo) == 0


def test_editing_graph_cypher_flows_through_to_both_json_files(tmp_repo):
    """README 的承諾：改 graph.cypher，再執行 radar-kg-build，兩個 JSON 就跟著變。"""
    cypher_path = tmp_repo / kg_source.CYPHER
    text = cypher_path.read_text(encoding="utf-8")
    assert "抬腳2分鐘 89~99次" in text
    cypher_path.write_text(text.replace("抬腳2分鐘 89~99次", "抬腳2分鐘 89~111次").replace("心肺耐力良好，持續中等", "心肺耐力很棒，持續中等"), encoding="utf-8")

    kg_source.main([], repo=tmp_repo)
    metrics = json.loads((tmp_repo / kg_source.METRICS_JSON).read_text(encoding="utf-8"))
    cardio = next(m for m in metrics["metrics"] if m["name"] == "心肺耐力")
    level3 = next(lv for lv in cardio["levels"] if lv["score"] == 3)
    assert level3["name"] == "抬腳2分鐘 89~111次" and level3["suggestion"].startswith("心肺耐力很棒")

    thresholds = json.loads((tmp_repo / kg_source.THRESHOLDS_JSON).read_text(encoding="utf-8"))
    cardio_t = next(m for m in thresholds["metrics"] if m["name"] == "心肺耐力")
    assert next(lv for lv in cardio_t["levels"] if lv["score"] == 3)["max"] == 111
    # 缺口補起來了，檢查器也應該看得出來
    assert kg_source.find_threshold_issues(cardio_t) == set()


def test_levels_keep_graph_cypher_order(repo):
    metrics = kg_source.parse_cypher((repo / kg_source.CYPHER).read_text(encoding="utf-8"))
    assert [m["name"] for m in metrics] == ["下肢肌力", "單腳站立", "來回行走", "心肺耐力", "步行速度"]
    assert [lv["score"] for lv in metrics[0]["levels"]] == [4, 3, 2, 1, 0]
    assert [lv["score"] for lv in metrics[4]["levels"]] == [3, 2, 1, 0]


def test_unknown_metric_needs_a_config_entry(tmp_repo, capsys):
    cypher_path = tmp_repo / kg_source.CYPHER
    cypher_path.write_text(cypher_path.read_text(encoding="utf-8").replace("下肢肌力", "新指標"), encoding="utf-8")
    assert kg_source.main([], repo=tmp_repo) == 2
    assert "METRIC_CONFIG" in capsys.readouterr().err


def test_empty_or_broken_cypher_is_a_clear_error(tmp_repo, capsys):
    cypher_path = tmp_repo / kg_source.CYPHER
    cypher_path.write_text("// 沒有任何節點\n", encoding="utf-8")
    assert kg_source.main([], repo=tmp_repo) == 2
    assert "解析不到任何等級" in capsys.readouterr().err

    broken = "CREATE (m1:Metric {name: \"x\"})\nCREATE (m1)-[:has_level]->(c9_9)-[:give_advice]->(s9_9)\n"
    cypher_path.write_text(broken, encoding="utf-8")
    assert kg_source.main([], repo=tmp_repo) == 2
    assert "找不到對應的節點定義" in capsys.readouterr().err


def test_dose_is_split_from_suggestion_and_newline_unescaped(repo):
    metrics = kg_source.parse_cypher((repo / kg_source.CYPHER).read_text(encoding="utf-8"))
    level = metrics[0]["levels"][0]
    assert "【建議數值】" not in level["suggestion"] and "\\n" not in level["suggestion"]
    assert level["recommended_dose"].startswith("每週")
