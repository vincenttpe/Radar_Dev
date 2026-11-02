"""產生 Demo 原型頁 demo/prototype.html（docs/11「Demo 原型頁」）。

頁面上照護人員看到的每一句話都來自 radar-caregiver（`caregiver_report`、`trend_text`）與 radar-ask（`pick`），
這裡只把它們放進畫面：事件依時間排成通知卡片，照護紀錄草稿逐行累積，最後是兩週使用趨勢。
資料是 data/samples_demo/ 的**模擬資料**，頁面頂端一直標明。

重新產生（改了 template.html、caregiver.py 的用語或模擬資料後都要跑；tests/test_prototype.py 會檢查）：
    python demo/build.py                  # → demo/prototype.html（可直接用瀏覽器開，不需網路也能顯示）
    python demo/build.py --fragment 路徑  # 不含 doctype 的版本，給 Artifact 發布用

實測資料版（docs/11「10/19 實測資料」）：先用 radar-field-data mask 遮罩，再
    python demo/build.py --events outputs/field/FIELD-01.jsonl --place "1 號廁所"
寫到 outputs/field_prototype.html（不會被 commit）。頁面橫幅改成「實測資料」，沒有兩週趨勢。
還有像 MAC 的字串、或輸出位置會被 commit 時，拒絕產生。
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import demo_page  # demo/ 底下的共用外殼；直接執行或測試時 demo/ 都在 sys.path
from page_data import PLACE, PLACEHOLDER, REPO, TEMPLATE, demo_data, field_out

HERE = Path(__file__).resolve().parent


def fragment(data: dict[str, Any]) -> str:
    return demo_page.fragment(TEMPLATE, PLACEHOLDER, data)


def page(data: dict[str, Any]) -> str:
    return demo_page.page(TEMPLATE, PLACEHOLDER, data)


FIELD_OUT = REPO / "outputs" / "field_prototype.html"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fragment", type=Path, help="改寫出不含 doctype 的版本到這個路徑（Artifact 發布用）")
    ap.add_argument("--events", type=Path, help="改用遮罩過的實測資料（radar-field-data mask 的輸出）")
    ap.add_argument("--place", default=PLACE, help=f"地點名稱，預設「{PLACE}」")
    args = ap.parse_args()
    if args.events:
        if args.fragment:
            raise SystemExit("錯誤：實測資料版不提供 --fragment；實測資料不發布到線上")
        try:
            out, data = field_out(FIELD_OUT), demo_data(args.events, args.place)
        except ValueError as e:
            raise SystemExit(f"錯誤：{e}") from e
        out.write_text(page(data), encoding="utf-8")
        print(f"寫入 {FIELD_OUT}（實測資料，不要 commit、不要發布）")
        raise SystemExit(0)
    data = demo_data()
    if args.fragment:
        args.fragment.write_text(fragment(data), encoding="utf-8")
        print(f"寫入 {args.fragment}")
    else:
        (HERE / "prototype.html").write_text(page(data), encoding="utf-8")
        print(f"寫入 {HERE / 'prototype.html'}")
