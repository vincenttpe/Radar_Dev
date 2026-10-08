"""kg/releases.json：雜湊與實際檔案一致、核准狀態合法、kg/v1/ 的每個檔案都登記了。

這個測試失敗通常代表改了 kg/v1/ 的檔案卻沒更新清單，請在 repo 根目錄執行
radar-kg-manifest 後再提交（並確認該版的核准狀態）。
"""
import copy

import pytest

from radar_agent import kg_manifest

APPROVAL_STATUS = {"pending", "approved"}
RELEASE_STATUS = {"working", "approved", "retired"}
SOURCE_STATUS = {"matches_v1", "differs_from_v1", "superseded"}


@pytest.fixture(scope="module")
def manifest():
    return kg_manifest.load()


def test_hashes_match_files(manifest):
    assert kg_manifest.compute(copy.deepcopy(manifest)) == manifest


def test_every_file_in_each_release_dir_is_registered(manifest):
    for release in manifest["releases"]:
        assert sorted(release["files"]) == kg_manifest.release_files(release["version"]), release["version"]


def test_release_status_values(manifest):
    for release in manifest["releases"]:
        assert release["status"] in RELEASE_STATUS
        assert release["approval"]["status"] in APPROVAL_STATUS


def test_approved_release_names_an_approver_and_date(manifest):
    for release in manifest["releases"]:
        approval = release["approval"]
        if approval["status"] == "approved":
            assert approval["approver"] and approval["date"], release["version"]
            assert release["status"] in {"approved", "retired"}
        else:
            assert release["status"] == "working", "未核准的版本必須標為 working"


def test_versions_are_unique(manifest):
    versions = [r["version"] for r in manifest["releases"]]
    assert len(versions) == len(set(versions))


def test_other_sources_are_listed_with_a_reason(manifest):
    paths = {s["path"] for s in manifest["other_sources"]}
    assert paths == {
        "kg/source/graph.docx",
        "kg/source/standard_suggestion.xlsx",
        "kg/dumps/neo4j-2026-05-10T15-50-58.dump",
    }
    for source in manifest["other_sources"]:
        assert source["status"] in SOURCE_STATUS
        assert source["reason"]


def test_check_mode_exit_codes(manifest, monkeypatch, tmp_path, capsys):
    assert kg_manifest.main(["--check"]) == 0
    # 清單被改壞時要回非零
    bad = copy.deepcopy(manifest)
    bad["releases"][0]["files"]["kg/v1/graph.cypher"] = "0" * 64
    monkeypatch.setattr(kg_manifest, "MANIFEST", tmp_path / "releases.json")
    kg_manifest.dump(bad)
    assert kg_manifest.main(["--check"]) == 1
