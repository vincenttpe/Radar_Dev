# Dashboard 對接說明（草案，2026-10-19）

給 Dashboard 負責人（James）：11 月中甲方 Demo 以官網 Dashboard 呈現雷達偵測結果的文字報告（`11_Demo規劃.md`「11 月中甲方 Demo」）。本 repo 提供下面三種 JSON，**文字都已經過輸出檢查**，Dashboard 只要排版，不要改字、不要自己算數字。

三種輸出都是命令列，先用檔案交換；正式用 API、寫檔、資料庫還是 MQTT，等週一清單 4-1、4-2 討論後再定。

| 用途 | 指令 | `schema` |
|---|---|---|
| 一個地點一段時間的報告 | `radar-caregiver 紀錄檔 --place "1 號廁所" --json` | `caregiver-report-v0` |
| 近 7 天與前 7 天的使用趨勢 | `radar-caregiver 紀錄檔 --place "1 號廁所" --trend --json` | `usage-trend-v0` |
| 照護人員打字提問 | `radar-ask 紀錄檔 --place "1 號廁所" --question "提問" --endpoint 網址 --model 模型名稱 --json` | `event-ask-v0` |

紀錄檔是雲端格式（`{timestamp, topic, resolution, payload}`，JSON 陣列或一行一筆）。地點名稱不放長者姓名。輸出檢查沒通過時結束碼是 1：`radar-caregiver` 不輸出任何文字；`radar-ask` 仍輸出 JSON，但 `answer` 是 `null`，原因在 `rejected`。

試跑可以用模擬資料：`data/samples_demo/bathroom_morning.jsonl`（一個上午）。

**限定時間範圍**（2026-10-23）：加上 `--from "2026-10-14 08:00" --to "2026-10-14 10:00"`，只整理這段時間的紀錄。含開始、不含結束，相鄰兩段不會重複算；時間和紀錄一樣不帶時區（A49）；時間看不懂或開始不早於結束時，結束碼是 2。時間讀不懂的紀錄分不出在不在範圍內，照樣列為無法判讀。

## 第一階段的流程（2026-10-23 使用者說明）

- Demo 由 Dashboard 提供 JSON，暫時不從 Jetson 直接收雷達資料（MQTT）。初期先用三天內的資料，UI 之後再討論。
- 第一階段只做居服員的內容。
- 流程：本 repo（Jetson）向 Dashboard 提出需求 → Dashboard 把 JSON 傳給 Jetson → Jetson 產生文字（先通過輸出檢查）→ 傳回 Dashboard → Dashboard UI 顯示給居服員。
- 三天的資料做不出「使用趨勢」：趨勢要比兩週、每週至少 5 天可比較（`caregiver.MIN_COUNTED_DAYS`），會顯示資料不足。居服員畫面要放什麼，等 UI 討論再定。
- 還沒定的：Jetson 怎麼向 Dashboard 要資料（網址、認證、參數）、文字傳回哪裡、多久更新一次、提問是即時呼叫還是同一條路。

### 流程確認頁（內部用，2026-10-23）

正式畫面由官網 Dashboard 做；內部先用測試看板暫代 Dashboard，確認流程走得通：`demo/flow_check.html`（`python demo/flow_build.py` 產生，模擬資料）。每個情境依序列出：① Dashboard 送出的請求（API 草案，還沒約定）→ ② Dashboard 有的資料 → ③ 本 repo 回傳的 JSON（`radar-caregiver --json --from --to` 的原始輸出）→ ④ 照原文顯示。

④ 仿官網 Dashboard 的 Devices 頁（2026-10-23 截圖）擺放，是建議的位置，還要和 James 確認：
- **Alerts 欄**：`events[]` 的處置提示，一則一行（時間＋地點＋提示）。沒有事件、但摘要寫「無法判斷」時（例如裝置離線），放摘要，不寫「沒有待處理的事件」；官網目前在全部裝置離線時仍顯示「No unresolved events.」，會讓人以為一切正常。
- **裝置卡片**：事件摘要一句，加上「資料到幾點」。數字卡先不放，避免摘要寫「無法判斷」時顯示「跌倒警報 0」。
- **點開卡片**：處置提示與照護紀錄草稿。
- 「警報已解除」（`level` 為 `resolved`）目前也列在 Alerts；官網這一欄的意思是未解除的事件，要不要只列未解除的，待和 James 確認。

頁面目前列出兩件待決（都會改到給讀者的輸出格式，屬於單向門，要人決定）：
- **數字要能表示「無法判斷」**：摘要寫「無法判斷」時，`counts.fall_alerts` 等欄位仍是 `0`；Dashboard 做成數字卡會顯示「跌倒警報 0」。在那之前，Dashboard 有斷線或沒有資料時不要只顯示數字卡，摘要一定要一起顯示。
- **回傳要附資料到哪裡為止**：要的範圍比資料長時（例如要到 14:00、資料只到 11:59），文字沒有寫出之後沒有資料，Dashboard 也分不出「沒有事件」和「資料還沒到」。

實測資料可以產生本機版：`python demo/flow_build.py --events 遮罩後的檔案 --place 地點 --range 開始 結束`，寫到 `outputs/flow_field.html`，不 commit、不發布。

### 資料交換 API 草案（2026-10-23，還沒和 Dashboard 約定）

Dashboard 的 Logs 頁已經能依時間篩選、匯出 NDJSON（2026-10-23 截圖），看起來後台本來就能依時間查詢（待確認）。API 約定之前，先用手動方式走一遍：Logs 頁設定時間範圍 → 打開 heartbeat、topic → Export NDJSON → `radar-caregiver --from --to --json`。

**① Jetson 向 Dashboard 要資料**：`GET /api/v1/devices/{裝置代號}/records?from=…&to=…`（網址只是示意）。

| 回應欄位 | 用來確定什麼 |
|---|---|
| `from`、`to`（日期＋時間＋時區） | 範圍含開始、不含結束；畫面上的 Logs 頁只看得到時間，API 要能指定日期與時區（A49） |
| `received_until`、`complete` | Dashboard 收到哪個時間點為止；分得出「沒有事件」和「資料還沒到」 |
| `model`、`firmware` | 決定用哪種格式解析（radar10 或 Radar60FL） |
| `settings`：這段時間有效的偵測設定與變更時間 | 跌倒、久待警報有沒有開，久待門檻多少（A52）；警報沒開時，報告不能寫「0 次」 |
| `records[]`：每筆附 `id`、`received_at` | 去重；斷線後補傳的資料和即時資料分開（A13、A14） |
| `count`、`next_cursor` | 確認全部拿到，沒有被截斷 |

範圍超過上限、`from` 晚於 `to`、查無裝置時回錯誤碼，不回空陣列：空陣列只代表這段時間真的沒有紀錄。

**② Jetson 把文字傳回 Dashboard**：內容是 `caregiver-report-v0`，Dashboard 照原文顯示。

待決（會改到給讀者的輸出，屬於單向門，要人決定）：
- 回傳要附 `range`、資料到哪裡為止，以及 `counts` 要能表示「無法判斷」（見上面「流程確認頁」）。
- 警報沒開時，報告怎麼寫（例如「跌倒警報未開啟，無法判斷」）。要先拿到 ① 的 `settings`。

### Dashboard 給的資料：和原本假設的格式不同（2026-10-23 實測）

使用者從 Dashboard 下載了某台現場雷達 5 天的資料（10/19 07:24–10/23 14:02，2,787 筆）；之後又下載了另一台 Radar60FL 6 天的資料（10/17–10/22，8,134 筆，打開了 heartbeat）。都是真實資料，不進 repo，MAC 一律換成代號。結果：

| 項目 | 本 repo 原本支援（radar10） | Radar60FL（兩台實測） |
|---|---|---|
| topic | `radar10/<裝置 ID>` | `Radar60FL/<裝置 ID>/sys/property/post` |
| 內容 | `{"event": "fall"、"presence"…}` | `{"method":"post","params":{...},"version":...}` |
| 欄位 | 事件種類 | `someoneExists`、`motionStatus`、`residentStatus`、`online`（值是字串，例如 "0"） |
| 心跳 | 約每 5 秒一筆 | 第一台匯出的資料裡沒有，晚上 19:00 到隔天 07:00 大多沒有資料。第二台打開 Logs 頁的「Show heartbeat」匯出後有：`heartBeat`，約每 60 秒一筆，晚上也有（A47） |
| 跌倒、久待、呼救 | 有 | 第一台 5 天內沒有。第二台 10/17 有 4 筆 `fallStatus: "2"`，值的意思、解除、久待與呼救怎麼送還不知道（A46） |

- **目前的程式讀不懂**：多層的 topic 被當成伺服器下給裝置的指令而略過，`radar-caregiver` 只會寫「沒有收到資料，無法判斷」。這是安全的失敗（不會寫成「跌倒 0 次」），但也什麼都產生不了。
- **在欄位意思確認之前不寫轉換程式**：新增輸入格式要改事件解析，屬於單向門「事件格式」。照猜的轉換，報告可能寫出不實的內容。要確認的事見 `docs/05` A45–A50。
- 確認之後要改的地方：
  - 事件解析要認得這個型號。
  - 沒有心跳的型號，離線判斷不能用「超過 30 秒沒資料」，要依型號調整（A47）。
  - 造訪次數要先合併很短的空檔（A50）。
  - 格式不含跌倒時，報告寫「無法判斷」，不寫「0 次」（A46）。

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
| `counts.ended_offline` | 資料結束前斷線的時間（之後沒再連上）；沒有就是 `null` | 時間軸上從這裡標灰到結尾 |
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
| `answer` | 答案，開頭寫出涵蓋的時段（例如「1 號廁所 10/14 08:00–11:59：」）；對不到題目或答案沒通過檢查時是 `null` |
| `menu` | 全部六題 `{id, text}`，對不到時讓照護人員自己點 |
| `model`、`prompt_version` | 用了哪個模型、哪一版 prompt |
| `seconds` | 模型對應花的秒數 |
| `from`、`to` | 答案涵蓋的資料時間。題目不解析「今天早上」這類時段，畫面要標出這段時間 |
| `rejected` | 對不到或不顯示的原因 |

畫面流程：

1. 照護人員在輸入框打字送出。
2. 有 `question`：先顯示 `confirm`，按「是」才顯示 `answer`；按「不是」就顯示 `menu`。
3. 沒有 `question`：直接顯示 `menu`。點選單時改呼叫 `radar-ask ... --pick 題目id --json`，不經過模型。
4. 答案旁固定標出 `model` 與 `seconds`，例如「在地模型 Gemma 4 E4B · 1.2 秒」，並標出 `from`–`to`。
5. 「在 Jetson 本機執行、提問與資料不出場域」照部署設定顯示：Dashboard 端設一個開關，實際部署在 Jetson 上才打開（2026-10-19 決定），不要寫死。

本 repo 不保存照護人員打的原文（可能含姓名），Dashboard 端也請不要寫進 log。

## 不在這次對接

- 知識圖譜等級與建議：11 月 Demo 只揭露為開發中，不放進 Dashboard。
- 個人報告：Demo 地點是多人使用的廁所，沒有個人 ID；不推測是哪一位長者。
- 照護紀錄改寫（在地模型版）：這次不展示。

## 要和 James 確認

| 題目 | 對應週一清單 |
|---|---|
| 輸出怎麼交給 Dashboard（API、寫檔、資料庫、MQTT），誰呼叫誰。**2026-10-23**：由 Jetson 向 Dashboard 要資料、產生文字後傳回（見「第一階段的流程」）；網址、認證、參數、更新頻率還沒定；草案見「資料交換 API 草案」 | 4-1 |
| Dashboard 給的 JSON 格式（Radar60FL）與欄位意思；Device Data 三種資料；當時有效的偵測設定 | —（見 `docs/05` A45–A52） |
| 上面三種 JSON 的欄位夠不夠、要不要改名 | 4-2 |
| 提問輸入框放在哪一頁；`radar-ask` 由 Dashboard 呼叫還是 Jetson 推送 | 4-1 |
| 照護紀錄草稿複製後寫回哪裡 | 4-4 |
| 讀者權限在哪一層控管 | 4-5 |
