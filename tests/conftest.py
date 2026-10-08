"""共用 fixture：repo 路徑與常用資料的讀取。

測試只檢查 repo 內的資料檔（知識圖譜、事件格式、標註、樣本），
不載入模型、不連 Neo4j、不連網。
"""
import csv
import json
from pathlib import Path

import pytest

from radar_agent import kg_source

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
    """{(指標, 分數): {name, rank, suggestion, recommended_dose}}，解析交給 kg_source。"""
    fields = ("name", "rank", "suggestion", "recommended_dose")
    return {
        (metric["name"], level["score"]): {f: level[f] for f in fields}
        for metric in kg_source.parse_cypher(graph_cypher)
        for level in metric["levels"]
    }


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
