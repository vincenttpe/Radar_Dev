"""共用 fixture：repo 路徑與常用資料的讀取。

測試只檢查 repo 內的資料檔（知識圖譜、事件格式、標註、樣本），
不載入模型、不連 Neo4j、不連網。
"""
import csv
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def repo() -> Path:
    return REPO


@pytest.fixture(scope="session")
def graph_cypher(repo) -> str:
    return (repo / "kg" / "v1" / "graph.cypher").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def metrics_json(repo) -> dict:
    return json.loads((repo / "kg" / "v1" / "metrics.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def kg_levels_from_cypher(graph_cypher) -> dict[tuple[str, int], dict]:
    """把 graph.cypher 解析成 {(指標, 分數): {name, rank, suggestion, recommended_dose}}。"""
    metric_names = dict(re.findall(r'\((m\d+):Metric \{name: "([^"]*)"\}\)', graph_cypher))
    criteria = {
        var: (name, int(score), rank)
        for var, name, score, rank in re.findall(
            r'CREATE \((c\d+_\d+):Criteria \{name: "([^"]*)", score: (\d+), rank: "([^"]*)"\}\)',
            graph_cypher,
        )
    }
    suggestions = dict(
        re.findall(r'CREATE \((s\d+_\d+):Suggestion \{content: "((?:[^"\\]|\\.)*)"\}\)', graph_cypher)
    )
    links = re.findall(
        r"\((m\d+)\)-\[:has_level\]->\((c\d+_\d+)\)-\[:give_advice\]->\((s\d+_\d+)\)", graph_cypher
    )

    levels = {}
    for m, c, s in links:
        name, score, rank = criteria[c]
        body = suggestions[s].replace("\\n", "\n")
        text, _, dose = body.partition("【建議數值】")
        levels[(metric_names[m], score)] = {
            "name": name,
            "rank": rank,
            "suggestion": text.strip(),
            "recommended_dose": dose.strip() or None,
        }
    return levels


@pytest.fixture(scope="session")
def label_rows(repo) -> list[dict]:
    with (repo / "data" / "labels" / "sorted_data.csv").open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="session")
def sample_files(repo) -> list[Path]:
    return sorted((repo / "data" / "samples").glob("*.json"))


@pytest.fixture(scope="session")
def event_schema(repo) -> dict:
    return json.loads((repo / "schemas" / "event.v0.draft.json").read_text(encoding="utf-8"))
