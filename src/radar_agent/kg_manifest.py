"""知識圖譜發布清單 kg/releases.json 的雜湊計算與檢查。

清單記錄每個版本的檔案內容雜湊與核准狀態，讓「同樣標 v1，內容卻不同」會被測試擋下。
改了 kg/v1/ 裡的檔案後，執行：

    radar-kg-manifest          # 重算雜湊並寫回清單（保留核准欄位）
    radar-kg-manifest --check  # 只檢查，不一致就以非零結束

需要先 pip install -e .（見 pyproject.toml）。

雜湊是檔案位元組的 SHA-256；請確保換行沒被轉換（本 repo 以 LF 提交）。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "kg" / "releases.json"


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def release_files(version: str) -> list[str]:
    """kg/<version>/ 底下所有檔案，repo 相對路徑、排序。"""
    base = REPO / "kg" / version
    return sorted(p.relative_to(REPO).as_posix() for p in base.rglob("*") if p.is_file())


def compute(manifest: dict) -> dict:
    """依目前檔案內容重算雜湊，回傳新的清單（核准欄位與說明原樣保留）。"""
    for release in manifest["releases"]:
        release["files"] = {f: sha256_of(REPO / f) for f in release_files(release["version"])}
    for source in manifest["other_sources"]:
        source["sha256"] = sha256_of(REPO / source["path"])
    return manifest


def load() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def dump(manifest: dict) -> None:
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    current = load()
    fresh = compute(json.loads(json.dumps(current)))
    if "--check" in argv:
        if fresh != current:
            print("kg/releases.json 與實際檔案不一致；請執行 radar-kg-manifest")
            return 1
        print("kg/releases.json 與實際檔案一致")
        return 0
    dump(fresh)
    print("已更新 kg/releases.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
