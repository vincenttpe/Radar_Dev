"""舊雛形 Project1.py 的 --input 路徑解析。

Project1.py 一 import 就載入模型並連 Neo4j，不能直接 import。
這裡把 __main__ 區塊裡的那段邏輯原樣抄出來測，並用一個測試確認原始碼
仍然是同一段，避免兩邊不同步。
"""
import re
from pathlib import Path

import pytest

SNIPPET = '''        if not input_file.is_absolute() and not input_file.is_file():
            input_file = DATA_DIR / input_file

        if not input_file.is_file():'''


def resolve_input(arg: str, data_dir: Path) -> Path:
    """與 Project1.py __main__ 內 `if args.input:` 分支相同的判斷。"""
    input_file = Path(arg)
    if not input_file.is_absolute() and not input_file.is_file():
        input_file = data_dir / input_file
    return input_file


def test_source_still_contains_the_same_logic(repo):
    src = (repo / "legacy" / "prototype" / "Project1.py").read_text(encoding="utf-8")
    assert SNIPPET in src
    assert re.search(r'DATA_DIR = REPO_DIR / "data" / "samples"', src)


@pytest.fixture
def data_dir(repo):
    return repo / "data" / "samples"


def test_bare_filename_resolves_into_data_samples(repo, data_dir, monkeypatch):
    monkeypatch.chdir(repo)
    p = resolve_input("01.json", data_dir)
    assert p == data_dir / "01.json" and p.is_file()


def test_bare_filename_resolves_from_any_cwd(repo, data_dir, monkeypatch):
    monkeypatch.chdir(repo / "legacy" / "prototype")
    p = resolve_input("01.json", data_dir)
    assert p == data_dir / "01.json" and p.is_file()


def test_relative_path_from_repo_root_is_used_as_is(repo, data_dir, monkeypatch):
    monkeypatch.chdir(repo)
    p = resolve_input("data/samples/01.json", data_dir)
    assert p == Path("data/samples/01.json") and p.is_file()


def test_absolute_path_untouched(data_dir):
    target = data_dir / "move.json"
    assert resolve_input(str(target), data_dir) == target


def test_directory_is_not_accepted(repo, data_dir, monkeypatch):
    monkeypatch.chdir(repo)
    p = resolve_input("data/samples", data_dir)
    assert not p.is_file()


def test_missing_file_reports_data_samples_path(repo, data_dir, monkeypatch):
    monkeypatch.chdir(repo)
    p = resolve_input("nope.json", data_dir)
    assert p == data_dir / "nope.json" and not p.is_file()
