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


def test_check_mode_passes_on_the_real_repo():
    assert kg_manifest.main(["--check"]) == 0


# ── 寫檔模式（radar-kg-manifest 不加 --check）─────────────────
# 用暫存目錄裡的小型 repo 測，不碰真正的 kg/，也不依賴真實資料的內容。

class TmpRepo:
    """暫存目錄裡的小型 repo，以及對它呼叫 kg_manifest 的方式。

    測試主體只透過這些方法操作；kg_manifest 指定 repo 的方式改變時，只需要改這個類別。
    """

    def __init__(self, root, monkeypatch):
        self.root = root
        self.manifest_path = root / "kg" / "releases.json"
        monkeypatch.setattr(kg_manifest, "REPO", root)
        monkeypatch.setattr(kg_manifest, "MANIFEST", self.manifest_path)

    def run(self, args):
        return kg_manifest.main(args)

    def load(self):
        return kg_manifest.load()

    def dump(self, manifest):
        kg_manifest.dump(manifest)

    def release_files(self, version):
        return kg_manifest.release_files(version)


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
            "approval": {"status": "pending", "approver": "王小明", "date": None},
            "note": "這段說明不能被覆蓋",
            "files": {},
        }],
        "other_sources": [{
            "path": "kg/source/old.docx", "status": "differs_from_v1", "reason": "用詞不同", "sha256": "",
        }],
    }
    repo = TmpRepo(tmp_path, monkeypatch)
    repo.dump(manifest)
    return repo


def test_write_mode_fills_hashes_and_keeps_hand_written_fields(tmp_repo):
    assert tmp_repo.run([]) == 0
    manifest = tmp_repo.load()
    release = manifest["releases"][0]
    assert release["files"] == {
        "kg/v1/a.cypher": kg_manifest.sha256_of(tmp_repo.root / "kg/v1/a.cypher"),
        "kg/v1/b.json": kg_manifest.sha256_of(tmp_repo.root / "kg/v1/b.json"),
    }
    assert manifest["other_sources"][0]["sha256"] == kg_manifest.sha256_of(tmp_repo.root / "kg/source/old.docx")
    # 手寫的欄位原樣保留
    assert manifest["note"] == "手寫的說明"
    assert release["approval"] == {"status": "pending", "approver": "王小明", "date": None}
    assert release["note"] == "這段說明不能被覆蓋" and release["status"] == "working"
    assert manifest["other_sources"][0]["reason"] == "用詞不同"


def test_check_fails_until_write_mode_is_run(tmp_repo, capsys):
    assert tmp_repo.run(["--check"]) == 1  # 雜湊還是空的
    assert "radar-kg-manifest" in capsys.readouterr().out
    tmp_repo.run([])
    assert tmp_repo.run(["--check"]) == 0


def test_check_fails_when_a_registered_hash_is_corrupted(tmp_repo):
    assert tmp_repo.run([]) == 0
    manifest = tmp_repo.load()
    manifest["releases"][0]["files"]["kg/v1/a.cypher"] = "0" * 64
    tmp_repo.dump(manifest)
    assert tmp_repo.run(["--check"]) == 1


def test_editing_a_file_changes_only_its_own_hash(tmp_repo):
    tmp_repo.run([])
    before = tmp_repo.load()["releases"][0]["files"]
    (tmp_repo.root / "kg/v1/a.cypher").write_text("CREATE (a), (b);\n", encoding="utf-8")
    assert tmp_repo.run(["--check"]) == 1
    tmp_repo.run([])
    after = tmp_repo.load()["releases"][0]["files"]
    assert after["kg/v1/a.cypher"] != before["kg/v1/a.cypher"]
    assert after["kg/v1/b.json"] == before["kg/v1/b.json"]
    assert tmp_repo.run(["--check"]) == 0


def test_new_file_is_registered_and_deleted_file_is_dropped(tmp_repo):
    tmp_repo.run([])
    (tmp_repo.root / "kg/v1/c.json").write_text("{}\n", encoding="utf-8")
    (tmp_repo.root / "kg/v1/b.json").unlink()
    assert tmp_repo.run(["--check"]) == 1
    tmp_repo.run([])
    assert sorted(tmp_repo.load()["releases"][0]["files"]) == ["kg/v1/a.cypher", "kg/v1/c.json"]


def test_write_mode_is_idempotent_and_keeps_chinese_readable(tmp_repo, capsys):
    tmp_repo.run([])
    first = tmp_repo.manifest_path.read_bytes()
    capsys.readouterr()
    assert tmp_repo.run([]) == 0
    assert tmp_repo.manifest_path.read_bytes() == first
    text = first.decode("utf-8")
    assert "王小明" in text and "\\u" not in text and text.endswith("\n")
    assert "已更新 kg/releases.json" in capsys.readouterr().out


def test_missing_registered_source_is_a_clear_error_not_a_traceback(tmp_repo, capsys):
    (tmp_repo.root / "kg/source/old.docx").unlink()
    assert tmp_repo.run([]) == 2
    assert "清單登記的檔案不存在" in capsys.readouterr().err
    assert tmp_repo.run(["--check"]) == 2


# ── 現有行為的固定（已知問題，另案處理）──────────────────
# 下面兩個測試記錄的是目前的行為，不是期望的行為；修正時要連測試一起改。

def test_missing_manifest_currently_raises_instead_of_a_clear_error(tmp_repo):
    """已知問題：releases.json 不存在時，main 直接丟出 FileNotFoundError（印出 traceback），不是結束碼 2。"""
    tmp_repo.manifest_path.unlink()
    with pytest.raises(FileNotFoundError):
        tmp_repo.run(["--check"])


def test_registered_version_without_a_directory_currently_passes_check(tmp_repo):
    """已知問題：清單登記的版本目錄不存在時，視為空版本，--check 照樣通過，寫檔模式也會寫入空的雜湊。"""
    assert tmp_repo.run([]) == 0
    manifest = tmp_repo.load()
    manifest["releases"].append({**manifest["releases"][0], "version": "v9", "files": {}})
    tmp_repo.dump(manifest)
    assert tmp_repo.release_files("v9") == []
    assert tmp_repo.run(["--check"]) == 0
    assert tmp_repo.run([]) == 0
    assert tmp_repo.load()["releases"][1]["files"] == {}


# ── 已核准的版本：核准綁的是核准時的內容 ──────────────────

def _approve(tmp_repo):
    """先登記雜湊，再把 v1 標成已核准（模擬審核人核准）。"""
    assert tmp_repo.run([]) == 0
    manifest = tmp_repo.load()
    manifest["releases"][0]["status"] = "approved"
    manifest["releases"][0]["approval"] = {"status": "approved", "approver": "王小明", "date": "2026-10-09"}
    tmp_repo.dump(manifest)
    return tmp_repo.manifest_path.read_bytes()


def test_approved_release_unchanged_is_fine(tmp_repo):
    _approve(tmp_repo)
    assert tmp_repo.run(["--check"]) == 0
    assert tmp_repo.run([]) == 0


def test_editing_an_approved_release_is_refused_and_the_manifest_is_untouched(tmp_repo, capsys):
    approved = _approve(tmp_repo)
    (tmp_repo.root / "kg/v1/a.cypher").write_text("CREATE (a), (被偷改);\n", encoding="utf-8")
    capsys.readouterr()

    assert tmp_repo.run([]) == 2
    err = capsys.readouterr().err
    assert "v1" in err and "新版本" in err
    assert tmp_repo.manifest_path.read_bytes() == approved  # 核准狀態與舊雜湊都沒被覆寫

    assert tmp_repo.run(["--check"]) == 1
    assert "已核准的版本內容被改過" in capsys.readouterr().out


@pytest.mark.parametrize("change", ["add", "delete"])
def test_adding_or_removing_a_file_in_an_approved_release_is_refused(tmp_repo, change):
    approved = _approve(tmp_repo)
    if change == "add":
        (tmp_repo.root / "kg/v1/new.json").write_text("{}\n", encoding="utf-8")
    else:
        (tmp_repo.root / "kg/v1/b.json").unlink()
    assert tmp_repo.run([]) == 2
    assert tmp_repo.manifest_path.read_bytes() == approved


def test_other_sources_may_change_even_when_the_release_is_approved(tmp_repo):
    """other_sources（舊 docx、備份）不屬於版本內容，雜湊可以照常更新。"""
    _approve(tmp_repo)
    (tmp_repo.root / "kg/source/old.docx").write_bytes(b"newer-source")
    assert tmp_repo.run([]) == 0
    assert tmp_repo.load()["releases"][0]["approval"]["status"] == "approved"
