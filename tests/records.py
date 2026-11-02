"""測試共用：把雲端格式的紀錄寫成紀錄檔（JSON Lines，一行一筆，docs/05 A2），給命令列測試讀。

故意寫壞格式的紀錄檔（空行、不是 JSON 的行）留在各自的測試裡手寫，看得出哪裡不正常。
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> Path:
    """一筆一行寫進 path（UTF-8，中文不轉義），回傳 path。"""
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records), encoding="utf-8")
    return path
