"""知識圖譜發布清單 kg/releases.json 的雜湊計算與檢查。

清單記錄每個版本的檔案內容雜湊與核准狀態，讓「同樣標 v1，內容卻不同」會被測試擋下。
改了 kg/v1/ 裡的檔案後，執行：

    radar-kg-manifest          # 重算雜湊並寫回清單（保留核准欄位）
    radar-kg-manifest --check  # 只檢查，不一致就以非零結束

需要先 pip install -e .（見 pyproject.toml）。

雜湊是檔案位元組的 SHA-256；請確保換行沒被轉換（本 repo 以 LF 提交）。

讀寫哪個 repo 由各函式的 repo 參數決定（預設是本 repo），清單路徑一律由它推出，
和 kg_source、kg_rules 的寫法一致；測試傳入暫存目錄即可，不必替換模組變數。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]


def _manifest_path(repo: Path) -> Path:
    return repo / "kg" / "releases.json"


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def release_files(version: str, repo: Path = REPO) -> list[str]:
    """kg/<version>/ 底下所有檔案，repo 相對路徑、排序。"""
    base = repo / "kg" / version
    return sorted(p.relative_to(repo).as_posix() for p in base.rglob("*") if p.is_file())


def compute(manifest: dict[str, Any], repo: Path = REPO) -> dict[str, Any]:
    """依目前檔案內容重算雜湊，回傳新的清單（核准欄位與說明原樣保留）。

    已核准版本的內容若有變動，由 main 拒絕寫入（見 changed_approved_releases）。
    """
    for release in manifest["releases"]:
        release["files"] = {f: sha256_of(repo / f) for f in release_files(release["version"], repo)}
    for source in manifest["other_sources"]:
        source["sha256"] = sha256_of(repo / source["path"])
    return manifest


def changed_approved_releases(current: dict[str, Any], fresh: dict[str, Any]) -> list[str]:
    """已核准、但檔案內容（或檔案清單）和核准時不同的版本。

    核准綁的是當時的內容：內容一變，原本的核准就不能沿用，必須建立新版本重新送審。
    """
    fresh_files = {r["version"]: r["files"] for r in fresh["releases"]}
    return [
        r["version"] for r in current["releases"]
        if r["approval"]["status"] == "approved" and r["files"] != fresh_files[r["version"]]
    ]


def load(repo: Path = REPO) -> dict[str, Any]:
    manifest: dict[str, Any] = json.loads(_manifest_path(repo).read_text(encoding="utf-8"))
    return manifest


def dump(manifest: dict[str, Any], repo: Path = REPO) -> None:
    _manifest_path(repo).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None, repo: Path = REPO) -> int:
    argv = sys.argv[1:] if argv is None else argv
    current = load(repo)
    try:
        fresh = compute(json.loads(json.dumps(current)), repo)
    except FileNotFoundError as e:
        print(f"清單登記的檔案不存在：{e.filename}", file=sys.stderr)
        return 2
    changed = changed_approved_releases(current, fresh)
    if "--check" in argv:
        if changed:
            print(f"已核准的版本內容被改過：{'、'.join(changed)}；核准不能沿用，請建立新版本重新送審")
            return 1
        if fresh != current:
            print("kg/releases.json 與實際檔案不一致；請執行 radar-kg-manifest")
            return 1
        print("kg/releases.json 與實際檔案一致")
        return 0
    if changed:
        print(
            f"已核准的版本內容被改過：{'、'.join(changed)}。核准綁的是核准時的內容，不能沿用；"
            "請還原這些檔案，或建立新版本（例如 kg/v1.1/）重新送審。kg/releases.json 未更新。",
            file=sys.stderr,
        )
        return 2
    dump(fresh, repo)
    print("已更新 kg/releases.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
