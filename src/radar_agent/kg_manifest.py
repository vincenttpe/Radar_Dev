"""知識圖譜發布清單 kg/releases.json 的雜湊計算與檢查。

清單記錄每個版本的檔案內容雜湊與核准狀態，讓「同樣標 v1，內容卻不同」會被測試擋下。
改了 kg/v1/ 裡的檔案後，執行：

    radar-kg-manifest          # 重算雜湊並寫回清單（保留核准欄位）
    radar-kg-manifest --check  # 只檢查，不一致就以非零結束

需要先 pip install -e .（見 pyproject.toml）。

雜湊是檔案位元組的 SHA-256；請確保換行沒被轉換（本 repo 以 LF 提交）。

其他程式要讀某一版知識圖譜時，先呼叫 verify_release：確認版本已登記、要讀的檔案和登記的雜湊相同，
並取得核准狀態。「什麼叫已核准」只在這個模組判斷。

讀寫哪個 repo 由各函式的 repo 參數決定（預設是本 repo），清單路徑一律由它推出，
和 kg_source、kg_rules 的寫法一致；測試傳入暫存目錄即可，不必替換模組變數。
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]


class KgIntegrityError(Exception):
    """知識圖譜檔案與發布清單登記的不一致，或清單沒有這一版。"""


@dataclass(frozen=True)
class VerifiedRelease:
    """verify_release 確認過的版本：要讀的檔案與登記的雜湊相同。"""

    version: str
    approval: str     # releases.json 的 approval.status（pending／approved…）
    approved: bool


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


def _is_approved(release: dict[str, Any]) -> bool:
    return bool(release["approval"]["status"] == "approved")


def verify_release(version: str, files: Iterable[str], repo: Path = REPO) -> VerifiedRelease:
    """確認 version 已登記，且 kg/<version>/ 底下的 files 內容與登記的雜湊相同。

    沒有登記這一版、或任一檔案的雜湊不同時丟 KgIntegrityError。只比對呼叫者要讀的檔案，
    版本裡其他檔案的變動不影響。
    """
    release = next((r for r in load(repo)["releases"] if r["version"] == version), None)
    if release is None:
        raise KgIntegrityError(f"kg/releases.json 沒有登記版本 {version!r}")
    for name in files:
        rel = f"kg/{version}/{name}"
        if release["files"].get(rel) != sha256_of(repo / rel):
            raise KgIntegrityError(f"{rel} 與 kg/releases.json 登記的雜湊不同，請先執行 radar-kg-build 與 radar-kg-manifest")
    return VerifiedRelease(version=version, approval=release["approval"]["status"], approved=_is_approved(release))


def changed_approved_releases(current: dict[str, Any], fresh: dict[str, Any]) -> list[str]:
    """已核准、但檔案內容（或檔案清單）和核准時不同的版本。

    核准綁的是當時的內容：內容一變，原本的核准就不能沿用，必須建立新版本重新送審。
    """
    fresh_files = {r["version"]: r["files"] for r in fresh["releases"]}
    return [
        r["version"] for r in current["releases"]
        if _is_approved(r) and r["files"] != fresh_files[r["version"]]
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
