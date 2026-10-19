# Dashboard 對接說明（草案，2026-10-19）

給 Dashboard 負責人（James）：11 月中甲方 Demo 以官網 Dashboard 呈現雷達偵測結果的文字報告（`11_Demo規劃.md`「11 月中甲方 Demo」）。本 repo 提供下面三種 JSON，**文字都已經過輸出檢查**，Dashboard 只要排版，不要改字、不要自己算數字。

三種輸出都是命令列，先用檔案交換；正式用 API、寫檔、資料庫還是 MQTT，等週一清單 4-1、4-2 討論後再定。

| 用途 | 指令 | `schema` |
|---|---|---|
| 一個地點一段時間的報告 | `radar-caregiver 紀錄檔 --place "1 號廁所" --json` | `caregiver-report-v0` |
| 近 7 天與前 7 天的使用趨勢 | `radar-caregiver 紀錄檔 --place "1 號廁所" --trend --json` | `usage-trend-v0` |
| 照護人員打字提問 | `radar-ask 紀錄檔 --place "1 號廁所" --question "提問" --endpoint 網址 --model 模型名稱 --json` | `event-ask-v0` |

紀錄檔是雲端格式（`{timestamp, topic, resolution, payload}`，JSON 陣列或一行一筆）。地點名稱不放長者姓名。輸出檢查沒通過時，指令的結束碼是 1，而且不輸出任何文字。

試跑可以用模擬資料：`data/samples_demo/bathroom_morning.jsonl`（一個上午）。

## 報告（`caregiver-report-v0`）

| 欄位 | 內容 | 畫面建議 |
|---|---|---|
| `place` | 地點 | 標題 |
| `from`、`to` | 資料涵蓋的時間（`YYYY-MM-DD HH:MM:SS`）；沒有資料時是 `null` | 標題旁 |
| `summary` | 事件摘要，一段話 | 卡片最上方 |
| `counts.visits` | 使用次數 | 數字卡 |
| `counts.fall_alerts` | 跌倒警報（雷達偵測），**不含**主動呼救 | 數字卡 |
| `counts.distress_calls` | 主動呼救 | 數字卡 |
| `counts.no_need_help` | 語音回覆沒事；**不是誤報**，不要寫成誤報（會議清單 C2） | 數字卡 |
| `counts.longstay` | 久待警報 | 數字卡 |
| `counts.offline` | 斷線時段 `[[開始, 結束], ...]` | 時間軸上標灰 |
| `events[]` | 每個事件一則：`at` 時間、`label` 顯示用時間（斷線是起訖）、`level`、`title`、`prompt` 處置提示、`record` 照護紀錄草稿 | 通知卡片；`record` 給「複製到照護紀錄」 |
| `text_layer` | 文字來源，目前都是 `template`（固定範本） | 小字標示 |

`level` 決定顏色：`urgent` 立即到場、`check` 前往查看、`resolved` 警報解除、`device` 裝置問題。

**一定要顯示**：有斷線時，`summary` 會寫「這段時間無法判斷是否有跌倒」，這句不能省略或改成「0 次」。

## 使用趨勢（`usage-trend-v0`）

| 欄位 | 內容 |
|---|---|
| `lines` | 要顯示的文字，一行一句，最後一句是「不代表特定長者」的說明，不能省略 |
| `days[]` | 14 天：`day` 日期、`visits` 使用次數、`dawn` 凌晨次數、`recent` 是否屬於近 7 天。在線不足的日子 `visits`、`dawn` 是 `null`，畫圖時要留空，不能畫成 0 |

## 提問（`event-ask-v0`）

這是 11 月 Demo 用來讓甲方感受**Jetson 上在地模型**的功能。模型只負責「選哪一題」，答案由事件事實套固定句型。

| 欄位 | 內容 |
|---|---|
| `question` | 對到的題目 `{id, text}`；對不到時是 `null` |
| `confirm` | 「您是想問「……」嗎？」 |
| `answer` | 答案；對不到題目或答案沒通過檢查時是 `null` |
| `menu` | 全部六題 `{id, text}`，對不到時讓照護人員自己點 |
| `model`、`prompt_version` | 用了哪個模型、哪一版 prompt |
| `seconds` | 模型對應花的秒數 |
| `rejected` | 對不到或不顯示的原因 |

畫面流程：

1. 照護人員在輸入框打字送出。
2. 有 `question`：先顯示 `confirm`，按「是」才顯示 `answer`；按「不是」就顯示 `menu`。
3. 沒有 `question`：直接顯示 `menu`。點選單時改呼叫 `radar-ask ... --pick 題目id --json`，不經過模型。
4. 答案旁標出 `model` 與 `seconds`，例如「在地模型 Llama-Breeze2 3B · 1.2 秒」；Demo 時說明模型在 Jetson 本機執行，提問與資料不出場域。實際是不是在 Jetson 上跑，要以部署為準，不要寫死在畫面上。

本 repo 不保存照護人員打的原文（可能含姓名），Dashboard 端也請不要寫進 log。

## 不在這次對接

- 知識圖譜等級與建議：11 月 Demo 只揭露為開發中，不放進 Dashboard。
- 個人報告：Demo 地點是多人使用的廁所，沒有個人 ID；不推測是哪一位長者。
- 照護紀錄改寫（在地模型版）：這次不展示。

## 要和 James 確認

| 題目 | 對應週一清單 |
|---|---|
| 輸出怎麼交給 Dashboard（API、寫檔、資料庫、MQTT），誰呼叫誰 | 4-1 |
| 上面三種 JSON 的欄位夠不夠、要不要改名 | 4-2 |
| 提問輸入框放在哪一頁；`radar-ask` 由 Dashboard 呼叫還是 Jetson 推送 | 4-1 |
| 照護紀錄草稿複製後寫回哪裡 | 4-4 |
| 讀者權限在哪一層控管 | 4-5 |
