# Dashboard 連線 API 草案（v0，2026-11-02，還沒和 Dashboard 約定）

給 Dashboard 負責人：Jetson 怎麼向 Dashboard 取需求、取裝置事件、傳回文字。連線方向見 `docs/adr/0009`（提議中）：
**Jetson 主動往外連，Dashboard 不連進 Jetson**。網址、欄位名稱都是草案，請逐項確認或改成你們的寫法；
本 repo 的連線程式（`src/radar_agent/dashboard_link.py`）把這些都集中在一處，改了只動那裡。

傳回的文字格式沿用 `docs/16`：報告 `caregiver-report-v0`、提問 `event-ask-v0`。

## 前提：真實裝置的格式還讀不懂

現場兩台都是 Radar60FL（`docs/16`「Dashboard 給的資料」），本 repo 目前只讀得懂 radar10，用真實資料只會產生
「沒有讀得懂的資料，N 筆紀錄無法判讀，無法判斷」（2026-11-02 以前是「沒有收到資料，無法判斷」，#64）。欄位意思要先由雷達負責人回答（`docs/05` A45–A50），之後另外加入解析（單向門「事件格式」）。
所以這一版先用模擬資料（radar10）把整條路走通。

### 要請雷達負責人回答（`docs/05` A45–A50 的濃縮）

| 題號 | 要問什麼 | 不知道答案會怎樣 |
|---|---|---|
| A45 | Radar60FL 的 `someoneExists`、`motionStatus`、`residentStatus`、`online` 各個值代表什麼 | 讀不出有沒有人、在不在動，報告只能寫「無法判斷」 |
| A46 | 這個型號會不會送跌倒、久待、主動呼救；`fallStatus: "2"` 是什麼意思、怎麼解除 | 不能寫跌倒次數；寫成「0 次」會讓人以為沒事 |
| A47 | 心跳多久一次；Logs 頁沒開「Show heartbeat」時，匯出的資料是不是就沒有心跳 | 分不出「裝置離線」和「沒人使用」 |
| A48 | 一直送離線、沒有恢復的那台，是裝置壞了還是網路問題 | 報告要寫「無法判斷」多久，沒有依據 |
| A49 | `version` 是韌體還是協定版本；時間戳是不是台灣時間 | 跨日、跨時區時，時段可能整段算錯 |
| A50 | 這台裝在哪裡、幾個人共用 | 使用次數要不要合併很短的空檔，沒有依據 |

## 流程

```
使用者 ─登入→ Dashboard（雲端）
                │ 1. 依登入 ID 鎖定機構與裝置，建立一筆需求（報告或提問）
                ▼
           需求佇列 ◀─ 2. Jetson 每 5 秒問一次「有沒有給我的需求」（GET jobs/next）
                │
                ├─ 3. Jetson 分頁取那段時間的裝置事件（GET records）
                │     在地模型與範本產生文字，通過輸出檢查
                └─ 4. Jetson 傳回 JSON（POST jobs/{id}/result）→ Dashboard 照原文顯示
```

- 只有第 2 步會帶提問原文；Jetson 不保存、不寫進 log。
- Jetson 停擺時需求留在佇列；Dashboard 等多久算逾時、畫面怎麼寫，請 Dashboard 決定（`docs/05` B10）。

## 認證

- 每台 Jetson 一個 token，放在 `Authorization: Bearer <token>`。token 決定它是哪個機構的 Jetson，只拿得到那個機構的需求與裝置。
- 一律 HTTPS，驗證憑證。token 不進 repo、不寫進 log；放在 Jetson 的哪裡由維運決定（B12）。Jetson 讀環境變數 `RADAR_DASHBOARD_TOKEN`。

## ① 取下一筆需求：`GET /api/v1/jetson/jobs/next`

- 沒有需求：`204 No Content`。
- 有需求：`200`，一次一筆。Jetson 取走後，這筆在 Dashboard 那邊標成「處理中」，不會再發給別台。

```json
{"job_id": "j-123", "kind": "report", "device": "DEMO-SIM-01", "place": "1 號廁所",
 "from": "2026-10-14 08:00:00", "to": "2026-10-14 12:00:00"}
```

| 欄位 | 說明 |
|---|---|
| `job_id` | Dashboard 自己的編號，傳回結果時帶回 |
| `kind` | `report`（報告）、`ask`（打字提問）或 `pick`（在選單上點題）；之後才加 `trend` |
| `device` | 裝置代號（不是 MAC） |
| `place` | 地點名稱，會寫進文字；不放長者姓名 |
| `from`、`to` | 範圍含開始、不含結束；格式 `YYYY-MM-DD HH:MM:SS`，和紀錄一樣不帶時區（A49） |
| `question` | 只有 `ask` 有：使用者打的提問原文（最多 100 字） |
| `question_id` | 只有 `pick` 有：選單上的題號，例如 `visits`（見下面「提問的兩種方式」） |

### 提問的兩種方式（2026-11-02 使用者說明）

Dashboard 依登入 ID 判斷要交給哪一台 Jetson，把那個機構的裝置事件交給 Jetson（②）。提問有兩種送法：

| 使用者在 Dashboard 上 | 需求 | Jetson 回 | Dashboard 顯示 |
|---|---|---|---|
| 在選單上點一題 | `pick` 加 `question_id` | `event-ask-v0`：固定句型的答案，不經過模型 | 顯示 `answer`；`answer` 是 `null`（沒通過輸出檢查）時改顯示 `notice` |
| 打字問 | `ask` 加 `question` | `event-ask-v0`：在地模型把提問歸類到選單的一題，同一次就附上確認句 `confirm` 和答案 `answer` | 先顯示 `confirm`（「您是想問……嗎？」）；按「對」才顯示 `answer`，按「不對」就顯示 `menu` 讓使用者點題 |
| 打字問，但歸類不到任何一題 | `ask` | `question`、`answer` 都是 `null`，附上提示句 `notice` 與 `menu` | 照原文顯示 `notice` 和 `menu` |

- 確認只要一次往返：答案和確認句一起回，使用者按「對」就能馬上看到答案，不必再等 Jetson 一輪（2026-11-02 決定）。
- 只要沒有 `answer`，就一定有提示句 `notice`，依原因不同（2026-11-02 決定由本 repo 產生、檢查用語，Dashboard 照原文顯示，不自己寫句子）：

  | 情況 | `notice` |
  |---|---|
  | 模型判斷沒有符合的題目，或回答格式不對 | 沒有對應到固定問題。請換個說法再問一次，或從選單選一題。 |
  | 這台 Jetson 沒有接在地模型、模型出錯或逾時 | 目前無法用打字提問，請從選單選一題。 |
  | 提問超過 100 字 | 提問太長（超過 100 字），請縮短後再問，或從選單選一題。 |
  | 沒有輸入提問 | 請輸入提問，或從選單選一題。 |
  | 對到題目（或點了題），但答案沒通過輸出檢查 | 這一題的答案暫時無法顯示，請通知資訊人員。 |

## ② 取裝置事件：`GET /api/v1/devices/{device}/records?from=…&to=…&cursor=…`

```json
{"records": [{"timestamp": "2026-10-14 08:00:00", "topic": "radar10/DEMO-SIM-01", "resolution": null,
              "payload": {"event": "absence"}}],
 "next_cursor": "c-2", "received_until": "2026-10-14 11:59:55", "complete": true,
 "settings": [{"since": "2026-10-14 00:00:00", "fallSwitch": true, "stillThreshold": 30}]}
```

| 欄位 | 說明 |
|---|---|
| `records` | 雲端格式 `{timestamp, topic, resolution, payload}`，和 Logs 頁匯出的 NDJSON 每一行相同 |
| `next_cursor` | 還有下一頁就給，最後一頁是 `null`。Jetson 會一直取到 `null` |
| `received_until` | Dashboard 收到這台裝置哪個時間點為止 |
| `complete` | 這段時間的資料是否都到了（要是 JSON 的 `true`／`false`；其他值一律當成沒到齊）。`false` 時，Jetson 照樣產生文字，並在傳回時附上 `received_until`，讓畫面寫「資料到幾點」 |
| `settings` | 這段時間有效的偵測設定與變更時間，例如 `[{"since": "…", "fallSwitch": true, "stillThreshold": 30}]`：跌倒、久待警報有沒有開、久待門檻多少（A52）。**請 Dashboard 現在就準備**；這一版的 Jetson 還不讀，等「警報沒開時報告怎麼寫」決定後才用（警報沒開時，報告不能寫「0 次」） |

範圍不合理、查無裝置時回錯誤碼（4xx），不要回空的 `records`：空的只代表這段時間真的沒有紀錄。

`docs/16`「資料交換 API 草案」另列的 `model`、`firmware`、每筆 `id`、`received_at`，這一版的 Jetson 還不用；
等 Radar60FL 確認後再加。

## ③ 傳回結果：`POST /api/v1/jetson/jobs/{job_id}/result`

```json
{"job_id": "j-123", "status": "done", "received_until": "2026-10-14 11:59:55", "complete": true,
 "result": {"schema": "caregiver-report-v0", "...": "..."}, "problems": []}
```

| `status` | 意思 | `result` |
|---|---|---|
| `done` | 產生了文字 | 報告是 `caregiver-report-v0`，提問與點題是 `event-ask-v0`，原樣顯示 |
| `rejected` | 文字沒通過輸出檢查，不交出任何文字 | `null`；原因在 `problems`，畫面寫「報告沒有產生」，不要自己補字 |
| `failed` | 取資料失敗等，沒辦法產生 | `null`；原因在 `problems` |

Dashboard 收到後怎麼回（Jetson 依此決定要不要再送）：

| Dashboard 回 | 意思 | Jetson 怎麼做 |
|---|---|---|
| `204`（任何 2xx 都算） | 收到了 | 不解析內容，算送到。但回應傳到一半就斷了的話，Jetson 不知道 Dashboard 收到沒有，會當成「回應不完整」再送一次（所以 Dashboard 要能接受同一個 `job_id` 的重複結果） |
| 4xx（408、429 除外），例如 `404` 查無此需求、`410` 已逾時 | 明確不收 | 丟掉並記下來，不再送 |
| 5xx、`408`、`429`，或連不上、回應不完整 | 暫時性問題 | 下一輪再送；補送 12 次（約 1 分鐘）還不行就丟掉並記下來，等 Dashboard 逾時重發 |

提問沒對到題目、或答案沒通過輸出檢查時，仍是 `done`：`event-ask-v0` 本身的 `answer` 是 `null`、原因在 `rejected`（`docs/16`「提問」）。

## 要和 Dashboard 負責人確認

| 題目 | 對應 |
|---|---|
| 網址、欄位名稱是否照這份，或改成你們的寫法 | `docs/14` 4-1 |
| 需求佇列放在 Dashboard 後台的哪裡；取走後多久沒傳回算逾時、要不要重發 | 4-1、B10 |
| 一個機構幾台 Jetson；token 怎麼發、怎麼撤銷 | B11、B12 |
| `records` 能不能分頁、給 `received_until` 與 `complete` | 4-2、A49 |
| 權限只在 Dashboard 控管，Jetson 完全信任送來的需求，可以嗎 | 4-5 |
| 雲端是 AWS、Azure、GCP 還是自建；有沒有現成的裝置身分機制（例如 AWS IoT Core、Azure IoT Hub） | B12 |
| 身分驗證用每台一個 token（本草案），還是每台一張憑證、用 mTLS（雲端確認是哪一台在連線，私鑰可放 Orin 的 fTPM） | B12 |
| 現場網路是 Wi-Fi、乙太網路還是 4G；是否只放行 443、要不要經 proxy | B11、B12 |
| 同一個 `job_id` 的結果可能收到兩次（第一次其實送到了、只是回應沒傳回來，Jetson 會再送）：Dashboard 收到重複的結果要不出錯，以最後一次為準或忽略都可以 | 4-1 |

## Jetson 端的部署注意（之後上線時）

| 項目 | 做法 | 現況 |
|---|---|---|
| 系統時間 | 很多 Jetson 載板沒有 RTC 電池，斷電重開後時間會回到 1970，HTTPS 憑證會驗證失敗。開機先完成 NTP 校時再啟動；systemd 設 `After=time-sync.target` | 連線程式啟動時檢查時間，明顯不對就停下來並提示校時 |
| 自動重啟 | systemd 設 `Restart=always`，程式掛掉後自動重啟 | 連不上 Dashboard 時程式本身不會停，等一下再試 |
| 轉址 | 不跟轉址：轉址時 token 會一起帶去新位置 | 收到 3xx 當成錯誤，下一輪再試 |
| 送不出去的結果 | 照 ③ 的表：暫時性問題下一輪再送，Dashboard 明確不收就丟掉 | 只留在記憶體；補送成功之前不取新需求，所以最多一筆；補送 12 次還不行就丟掉，讓後面的需求能繼續。程式重啟也會丟掉，所以 Dashboard 那邊要有逾時重發（上面要確認的題目） |
| Dashboard 回的資料不合草案 | 這筆需求傳回 `failed` 並寫原因；連需求本身都不是物件就記下來、略過 | 服務不會因此停掉；輪詢迴圈另外攔下所有意料外的錯誤（只記種類），systemd 的 `Restart=always` 是最後防線 |
| CA 憑證 | 舊版 JetPack 的 `ca-certificates` 太舊時，連新的雲端端點會失敗 | 要納入更新範圍（B9、B11，維運決定） |
| 遠端維運 | 不靠固定 IP 加開 port；用 Tailscale、WireGuard 或雲端的 secure tunnel | 目前用 AnyDesk；換不換由維運決定（B11、B12） |
| 更新 | 程式與模型的更新要能驗證來源、能回滾 | 還沒有，屬維運（B9、B11） |

跌倒即時警示不經過這條連線（ADR 0006），這條路斷了不會漏掉警示；這裡傳回的只是報告與提問的文字。

## 本機試跑（不連真實雲端）

`demo/fake_dashboard.py` 是照這份草案寫的假 Dashboard，只聽 127.0.0.1，資料是 `data/samples_demo/` 的模擬上午：

```bash
python demo/fake_dashboard.py
```

```bash
RADAR_DASHBOARD_TOKEN=demo-token python -m radar_agent.dashboard_link --base http://127.0.0.1:8770 --once --echo
```

假 Dashboard 會在終端機印出 Jetson 傳回的每一筆結果。`--echo` 改成 `--endpoint http://127.0.0.1:11434/v1 --model gemma4:e4b`
就是用 Jetson 上的在地模型選提問的題號。
