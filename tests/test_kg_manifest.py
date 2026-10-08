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


# ── 寫檔模式（radar-kg-manifest 不加 --check）─────────────────
# 用暫存目錄裡的小型 repo 測，不碰真正的 kg/，也不依賴真實資料的內容。

@pytest.fixture
def tmp_repo(tmp_path, monkeypatch):
    (tmp_path / "kg" / "v1").mkdir(parents=True)
    (tmp_path / "kg" / "v1" / "a.cypher").write_text("CREATE (a);\n", encoding="utf-8")
    (tmp_path / "kg" / "v1" / "b.json").write_text('{"x": 1}\n', encoding="utf-8")
    (tmp_path / "kg" / "source").mkdir()
    (tmp_path / "kg" / "source" / "old.docx").write_bytes(b"old-source")
    manifest = {
        "note": "手寫的說明",
        "releases": [{
            "version": "v1",
            "status": "working",
            "approval": {"status": "approved", "approver": "王小明", "date": "2026-10-08"},
            "note": "這段說明不能被覆蓋",
            "files": {},
        }],
        "other_sources": [{
            "path": "kg/source/old.docx", "status": "differs_from_v1", "reason": "用詞不同", "sha256": "",
        }],
    }
    monkeypatch.setattr(kg_manifest, "REPO", tmp_path)
    monkeypatch.setattr(kg_manifest, "MANIFEST", tmp_path / "kg" / "releases.json")
    kg_manifest.dump(manifest)
    return tmp_path


def test_write_mode_fills_hashes_and_keeps_hand_written_fields(tmp_repo):
    assert kg_manifest.main([]) == 0
    manifest = kg_manifest.load()
    release = manifest["releases"][0]
    assert release["files"] == {
        "kg/v1/a.cypher": kg_manifest.sha256_of(tmp_repo / "kg/v1/a.cypher"),
        "kg/v1/b.json": kg_manifest.sha256_of(tmp_repo / "kg/v1/b.json"),
    }
    assert manifest["other_sources"][0]["sha256"] == kg_manifest.sha256_of(tmp_repo / "kg/source/old.docx")
    # 手寫的欄位原樣保留
    assert manifest["note"] == "手寫的說明"
    assert release["approval"] == {"status": "approved", "approver": "王小明", "date": "2026-10-08"}
    assert release["note"] == "這段說明不能被覆蓋" and release["status"] == "working"
    assert manifest["other_sources"][0]["reason"] == "用詞不同"


def test_check_fails_until_write_mode_is_run(tmp_repo, capsys):
    assert kg_manifest.main(["--check"]) == 1  # 雜湊還是空的
    assert "radar-kg-manifest" in capsys.readouterr().out
    kg_manifest.main([])
    assert kg_manifest.main(["--check"]) == 0


def test_editing_a_file_changes_only_its_own_hash(tmp_repo):
    kg_manifest.main([])
    before = kg_manifest.load()["releases"][0]["files"]
    (tmp_repo / "kg/v1/a.cypher").write_text("CREATE (a), (b);\n", encoding="utf-8")
    assert kg_manifest.main(["--check"]) == 1
    kg_manifest.main([])
    after = kg_manifest.load()["releases"][0]["files"]
    assert after["kg/v1/a.cypher"] != before["kg/v1/a.cypher"]
    assert after["kg/v1/b.json"] == before["kg/v1/b.json"]
    assert kg_manifest.main(["--check"]) == 0


def test_new_file_is_registered_and_deleted_file_is_dropped(tmp_repo):
    kg_manifest.main([])
    (tmp_repo / "kg/v1/c.json").write_text("{}\n", encoding="utf-8")
    (tmp_repo / "kg/v1/b.json").unlink()
    assert kg_manifest.main(["--check"]) == 1
    kg_manifest.main([])
    assert sorted(kg_manifest.load()["releases"][0]["files"]) == ["kg/v1/a.cypher", "kg/v1/c.json"]


def test_write_mode_is_idempotent_and_keeps_chinese_readable(tmp_repo, capsys):
    kg_manifest.main([])
    first = kg_manifest.MANIFEST.read_bytes()
    capsys.readouterr()
    assert kg_manifest.main([]) == 0
    assert kg_manifest.MANIFEST.read_bytes() == first
    text = first.decode("utf-8")
    assert "王小明" in text and "\\u" not in text and text.endswith("\n")
    assert "已更新 kg/releases.json" in capsys.readouterr().out


def test_missing_registered_source_is_a_clear_error_not_a_traceback(tmp_repo, capsys):
    (tmp_repo / "kg/source/old.docx").unlink()
    assert kg_manifest.main([]) == 2
    assert "清單登記的檔案不存在" in capsys.readouterr().err
    assert kg_manifest.main(["--check"]) == 2
