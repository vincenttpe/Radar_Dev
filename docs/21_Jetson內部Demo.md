# Jetson 內部 Demo（草案，2026-10-28）

在 Demo 機台（Orin NX 16GB，`docs/05` B1）上，**頁面、服務、在地模型都在同一台 Jetson**，畫面接在 Jetson 的螢幕上。目的是讓團隊先實際體會「打字提問、在地模型在這台 Jetson 上選題」，再決定對甲方 Demo 要怎麼演（`docs/11`）。只用模擬資料，內部使用。

## 架構

```
Jetson 螢幕
└─ 瀏覽器全螢幕開 http://127.0.0.1:8765/#replay
      │  打字提問：POST /api/ask（只送提問文字）
      ▼
demo/jetson_serve.py（Python 標準函式庫的 HTTP 服務，只聽 127.0.0.1）
  ├─ GET /           原型頁（和 demo/build.py 產生的相同，另外標記 Jetson 模式）
  ├─ POST /api/ask   event_ask.ask_events()：模型只選題號；答案是固定句型，通過輸出檢查
  └─ GET /api/stats  tegrastats 的最新一筆（GPU 使用率、記憶體、溫度）
      │
      ▼
Ollama gemma4:e4b（127.0.0.1:11434，GPU）
```

頁面和靜態原型頁（`demo/prototype.html`）是同一個範本，句子來源相同（`radar-caregiver`、`radar-ask --pick`）。Jetson 模式只多了：

- **打字提問接上模型**：先顯示「您是想問「……」嗎？」，按〔對〕才顯示答案，按〔不是〕或對不到時請讀者從選單點。確認句標出模型名稱、耗時與「在這台 Jetson 上執行」；答案旁註明「固定句型，不是模型寫的」。
- **對話泡泡**：提問與回覆依序排下來。打的字只留在畫面上，重播或重新整理就消失。
- **Jetson 狀態**：頂端橫幅顯示 GPU 使用率、記憶體與溫度（讀得到 `tegrastats` 時）。

不做的：追問（「那昨天呢？」）、一句問兩件事、時段解析。這些要擴充模型的回覆格式，屬於單向門「給讀者的輸出與用語」，題目與時段選項要先由審核人確認（`docs/06`「4.1」）。

## 安全

- 服務只聽 `127.0.0.1`，沒有改聽其他網卡的參數；同網段的電腦連不進來。
- `--endpoint` 只接受本機（127.0.0.1、localhost、::1），提問不會送到區網上的其他機器。
- 讀者打的原文不寫進本服務的 log、不存檔：log 只記方法、路徑與狀態碼（`tests/test_jetson_serve.py` 檢查）。
- 畫面只寫得出能驗證的說法：「在這台 Jetson 上執行」「提問不寫進本服務的紀錄」。原本的「資料不出這台 Jetson」要等確認 Ollama 不記錄 prompt 之後才能恢復（2026-10-28 決定；見下方待核對）。
- 請求帶 `Origin` 而且不是這個頁面時拒絕（403），避免 Jetson 瀏覽器開著的其他網站借這個服務送提問。
- 這個服務把讀者打的字送進模型，屬於單向門「個人資料的流向」（`REVIEW.md` 第 2 節；`pr_gate.py` 的 `DOORS` 已列入）。
- Ollama 會不會記錄 prompt 要確認並關掉（`docs/20` 第 3 步）。

## 啟動步驟

前提：`docs/20` 第 1～3 步做完（Python 3.11 venv、`pip install -e .`、Ollama 在 GPU 上跑 `gemma4:e4b`）。

1. **先量一次準確率與延遲**。Gemma 4 在 Ollama 預設開著思維鏈，一定要加 `--reasoning-effort none`（2026-10-28 在 Demo 機台實測：關掉後每題約 0.4～0.5 秒，開著時約 8.7 秒，`docs/06`「4.1」）：

   ```bash
   radar-model-eval --endpoint http://127.0.0.1:11434/v1 --model gemma4:e4b --reasoning-effort none --event-questions
   ```

2. **啟動服務**（在 repo 根目錄、用 repo 的 venv）：

   ```bash
   python demo/jetson_serve.py --endpoint http://127.0.0.1:11434/v1 --model gemma4:e4b --reasoning-effort none
   ```

   啟動後馬上印出網址，頁面就能開；同時在背景問一題暖機，完成時印出「暖機完成：x.x 秒」（第一次載入模型要十幾秒）。印出「暖機失敗」時看後面的原因，常見的是 Ollama 沒開或模型名稱打錯；這時頁面仍能開，但打字提問會改請人從選單點。找不到 `tegrastats` 時只是不顯示 Jetson 狀態。`--port` 換埠號，`--no-warmup`、`--no-stats` 關掉暖機與狀態。

3. **在 Jetson 的螢幕上開頁面**：Demo 機台有 Firefox（2026-10-28 確認）。用一個專用的設定檔開全螢幕（kiosk），按 Alt+F4 離開：

   ```bash
   mkdir -p ~/radar-demo-firefox
   firefox --kiosk --new-instance --profile ~/radar-demo-firefox http://127.0.0.1:8765/#replay
   ```

   - 用專用設定檔是因為 Firefox 已經開著時，`--kiosk` 只會在原本的視窗開新分頁，不會進全螢幕；專用設定檔也不會帶到平常瀏覽的分頁與書籤。
   - 設定檔目錄不要用 `.` 開頭：Ubuntu 22.04 的 Firefox 是 snap 版，讀不到家目錄裡的隱藏目錄（待核對）。
   - 第一次開可能會跳出歡迎頁或「設為預設瀏覽器」的詢問，先在這個設定檔裡關掉一次，展示時就不會再出現。

4. **展示**：按「下一個事件」重播完，再打字提問。建議的順序：
   1. 先點選單問一題，說明原本的做法；
   2. 打「有人跌到嗎」（故意打錯字），看確認句與耗時，按〔對〕；
   3. 打「斷線」這種只有兩個字的問法；
   4. 打「他今天血壓多少」，系統不回答醫療問題，改請人從選單點；
   5. 拔掉網路線再問一次，說明模型就在這台 Jetson 上。

## 頁面打不開時

Firefox 顯示「Unable to connect／無法連線」代表 `127.0.0.1:8765` 沒有服務在聽：

1. 看跑 `jetson_serve.py` 的終端機：有沒有印出「開啟 http://127.0.0.1:8765/#replay」。沒有的話看錯誤訊息（例如 `No such file or directory`：還沒切到有這個檔的分支或版本）。
2. 服務要一直開著：關掉那個終端機或按 Ctrl+C，服務就停了。瀏覽器要在**另一個**終端機開。
3. 確認埠有在聽：`ss -ltn | grep 8765`；或 `curl -s http://127.0.0.1:8765/ | head -c 80` 要看到 `<!doctype html>`。

## 待核對（在 Demo 機台上）

| 項目 | 怎麼確認 |
|---|---|
| Jetson 有沒有接螢幕、桌面環境 | 有 Firefox（2026-10-28 確認）；螢幕與桌面用 `scripts/jetson_check.sh` 或直接看。第 3 步的 kiosk 指令在 snap 版 Firefox 上能不能用待核對 |
| Ollama 會不會記錄 prompt | `journalctl -u ollama` 裡有沒有提問原文（`OLLAMA_DEBUG` 要關）；確認不記錄後，畫面才能恢復「資料不出這台 Jetson」的說法 |
| `tegrastats` 的格式 | `demo/jetson_serve.py` 依 JetPack 6 的格式讀 `RAM`、`GR3D_FREQ`、`tj@`／`gpu@`；讀不到的項目不顯示 |
| 執行帳號 | 用 `docs/20` 第 1 步的專用帳號跑服務；瀏覽器用登入桌面的帳號開即可（只連 127.0.0.1） |

## 2026-10-28 在 Demo 機台上

切到本分支後照第 2、3 步啟動：先印出網址，暖機完成 13.1 秒（`gemma4:e4b`，`--reasoning-effort none`；和 `docs/06`「4.1」量到的模型載入 12.83 秒相當），Firefox 打得開頁面。打字提問的耗時、Jetson 狀態列與 kiosk 全螢幕待補記。

## 2026-10-28 在雲端容器的試跑

在雲端容器（x86 CPU，llama-cpp-python 0.3.36 跑 Gemma 4 E4B Q4_K_M）上用瀏覽器實際走過：暖機 0.7 秒；「有人跌到嗎」→「有沒有人跌倒？」0.72 秒，按〔對〕顯示的答案與 `radar-ask --pick falls` 相同；「他今天血壓多少」→ 沒有對應到；重播時對話清空。Jetson 狀態是用假的 `tegrastats` 試的。**不是在 Jetson 上跑的**，速度與顯示都要在 Demo 機台上再確認。
