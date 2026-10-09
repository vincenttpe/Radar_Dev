---
name: weekly-report
description: 用 radar-weekly-report 產生甲方週報（每週跌倒次數與活動量）。使用者要「週報」「甲方週報」「這週跌倒幾次」「某機構某週的活動量」，或要把動作描述 JSON 整理成週報時使用。只統計、不經知識圖譜與 LLM；建議報告不在這一版。
---

# 甲方週報

把動作描述 JSON（`Timestamp`／`Motion`／`Speed`／`fall`）整理成一週的跌倒次數與活動量。統計由程式 `src/radar_agent/weekly_report.py` 完成，這個 skill 只負責選輸入、執行、交付。

## 步驟

1. **確認指令可用**：`radar-weekly-report --help`。找不到指令就在 repo 根目錄執行 `pip install -e ".[dev]"`（必須是可編輯安裝，程式會讀 repo 裡的 `schemas/`、`kg/`）。
2. **確定三個參數**，使用者沒講的照下面的預設，不用另外問：
   - `--input`：動作描述 JSON 檔。沒指定時用 `data/samples_fall/` 裡日期最新的檔案，並在回覆中說明用的是樣本資料。
   - `--week-start YYYY-MM-DD`：週起始日。沒指定時省略，程式會取最新一筆紀錄所在週的星期一。
   - `--title`：報告名稱，通常是機構名稱。沒指定就省略。
3. **執行**：
   ```bash
   radar-weekly-report --input <檔案> [--week-start YYYY-MM-DD] [--title 機構名稱]
   ```
   要給其他程式用時加 `--json`，輸出欄位以 `WeeklyReport`（TypedDict）為準。
4. **交付**：把 Markdown 原樣交給使用者，或依使用者要求存檔。使用者要分享給其他人時，可以發布成 Docs artifact。

## 必須遵守

- **不改數字、不重算、不補推論**。報告裡的每個數字都來自程式；要改統計口徑，改的是 `counts_as_fall()`、`counts_as_activity()`，而且要等會議清單 C1、C2 有答案（`docs/05_會議待詢問清單.md`），不在這個 skill 裡處理。
- **「統計口徑與資料品質」一節要完整保留**。口徑是第 1 版暫定值，拿掉這一節數字會被誤讀。報告註明「全週總數無法確認」時，轉述也要照講，不能說成確定的次數。
- **不加健康建議**。週報第 1 版不接知識圖譜；使用者要建議時，說明這一版沒有，等級對應可另外用 `radar-kg-level`。
- 輸入只支援動作描述 JSON。裝置事件（`{"mode","event","trigger","userReply"}`）沒有時間戳，程式不接受；遇到時說明原因（會議清單 A1、A3、A4），不要自行補時間。
- 指令以非零結束（例如「輸入必須是 JSON 陣列」「沒有可用的紀錄」）時，把錯誤訊息原文告訴使用者並說明怎麼修，不要繞過。
