"""事件驗證：JSON Schema（含日期格式）加上 Schema 表達不了的時間順序規則。

事件格式仍是草案（schemas/event.v0.draft.json），改成 v1 時 SCHEMA_PATH 一起改。
需要 Python 3.11 以上（datetime.fromisoformat 要能解析 RFC 3339 的時區）。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "event.v0.draft.json"

# 時間欄位的先後規則：(較早, 較晚)。Schema 沒辦法表達，所以在這裡檢查。
# 只列從欄位名稱就能確定順序的；其他（例如 detected 與 started 的先後）待雷達端確認。
TIME_ORDER = [
    ("started", "ended"),
    ("voice_asked", "voice_answered"),
]


def _load_validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    # format_checker 需要 rfc3339-validator，否則 date-time 不會被檢查（見 requirements-dev.txt）
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


_VALIDATOR = _load_validator()


def validate_event(event: dict[str, Any]) -> list[str]:
    """回傳錯誤訊息清單；空清單代表通過。不丟例外：任何解析失敗都轉成錯誤訊息。"""
    errors = sorted(
        f'{".".join(str(p) for p in e.absolute_path) or "(根)"}：{e.message}'
        for e in _VALIDATOR.iter_errors(event)
    )
    if errors:  # 格式不對就不比時間順序，避免對壞資料重複報錯
        return errors

    stamps = event.get("timestamps", {})
    for earlier, later in TIME_ORDER:
        if earlier not in stamps or later not in stamps:
            continue
        try:
            out_of_order = parse_instant(stamps[later]) < parse_instant(stamps[earlier])
        except ValueError as e:
            errors.append(f"timestamps：無法比較 {earlier} 與 {later} 的先後（{e}）")
            continue
        if out_of_order:
            errors.append(f"timestamps：{later} 不得早於 {earlier}")
    return errors


def parse_instant(text: str) -> datetime:
    """RFC 3339 的時間 → 有時區的 datetime。

    RFC 3339 允許小寫的 t 與 z（Schema 的格式檢查也接受），但 datetime.fromisoformat 不接受，
    所以先轉大寫。仍解析不了（例如 RFC 3339 允許的閏秒 :60）就丟 ValueError，由呼叫端轉成錯誤訊息。
    """
    return datetime.fromisoformat(text.upper())
