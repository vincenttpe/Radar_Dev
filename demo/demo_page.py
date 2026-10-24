"""Demo 頁面的共用外殼：把資料嵌進範本，或包成完整的 HTML 頁面。

build.py（居服員原型）、dashboard_build.py（看板）、kg_build.py（知識圖譜）共用；
各頁的「commit 進去的頁面等於產生結果」測試靠這裡的輸出逐字一致。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HEAD = (
    '<!doctype html>\n<html lang="zh-Hant">\n<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
)


def fragment(template: Path, placeholder: str, data: dict[str, Any]) -> str:
    """範本裡的 placeholder 換成資料 JSON；「</」跳脫，資料裡的文字不會提早結束 <script>。"""
    payload = json.dumps(data, ensure_ascii=False, indent=1).replace("</", "<\\/")
    return template.read_text(encoding="utf-8").replace(placeholder, payload)


def page(template: Path, placeholder: str, data: dict[str, Any]) -> str:
    """完整的 HTML 頁面（可以直接開）。"""
    return HEAD + fragment(template, placeholder, data) + "</html>\n"
