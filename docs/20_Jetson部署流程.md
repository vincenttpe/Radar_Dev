# Jetson 部署流程（草案，2026-10-26）

把本 repo 部署到現場 Jetson PC 的步驟。型號怎麼選見 `06_Jetson硬體評估.md`；第一階段和 Dashboard 怎麼交換資料見 `16_Dashboard對接說明.md`。下面標「待核對」的是還沒在實機上確認的事，拿到 Jetson 後要先驗證。

## 先知道的四件事

1. **Python 版本**：本 repo 要求 Python 3.11 以上（`pyproject.toml`）。JetPack 6.x 是 Ubuntu 22.04，內建 Python 3.10（待核對），要另外裝 3.11 以上（例如 `uv` 或 deadsnakes）。
2. **只能用 git checkout 加可編輯安裝**：`kg_rules`、`kg_source`、`kg_manifest`、`event_validation` 用 `Path(__file__).parents[2]` 讀 repo 裡的 `kg/` 與 `schemas/`（`pyproject.toml` 也有註明）。打包成 wheel 安裝，執行時會找不到知識圖譜。
3. **和 Dashboard 交換資料的程式還沒有**：目前只有命令列（`radar-caregiver --json`、`radar-ask --json`）。`docs/16`「第一階段的流程」的「Jetson 向 Dashboard 要資料」與「把文字傳回 Dashboard」兩段都要另寫，API 也還沒約定（第 4 步）。
4. **跌倒即時警示不經過本 repo**（`docs/adr/0006`）：本 repo 部署失敗不影響警示，可以分階段上線。

## 0. 開始前要有答案

| 題號 | 問題 | 卡住哪一步 |
|---|---|---|
| B1／週一 3-1 | Jetson 型號、記憶體、JetPack 版本、可用磁碟 | 模型大小、Python 怎麼裝 |
| B2／3-2 | Jetson 上還跑什麼（MQTT broker、radarLLM、音訊檔目錄） | 資源分配、要隔離的目錄 |
| B5／3-3 | 能不能連外網 | 線上安裝，或先在別台打包再離線帶上去 |
| B11 | 誰維護、能不能遠端登入與更新、程式失敗通知誰、日誌存哪 | 第 5、7 步 |
| B12 | 磁碟加密、MQTT 與 HTTPS 的加密與認證、實體上誰碰得到 | 資安設定 |
| 3-4／F2 | 在地模型選哪個；授權能否商用、能否在機構現場部署 | 第 3 步 |
| 週一 4-1、4-2 | Dashboard 的 API：網址、認證、參數、文字傳回哪裡 | 第 4 步 |

## 1. 準備 Jetson

- 刷好 JetPack，記下版本與電源模式（`06` 第 4 節：結果才能重現、比較）。
- 裝 Python 3.11 以上與 `git`。
- 開專用帳號（例如 `radar`），程式不用 root 跑。
- 目錄：
  - `/opt/radar_agent/releases/<版本>`：程式，`/opt/radar_agent/current` 連到使用中的版本；
  - `/var/lib/radar_agent`：紀錄與輸出（屬個人資料，見 B12）；
  - `/opt/models`：模型檔。
- 用檔案權限讓 `radar` 帳號讀不到音訊檔目錄（`docs/adr/0005`）。

## 2. 安裝程式

- 部署用的版本在 main 打 tag（例如 `v0.1-demo`），只部署 tag，不部署任意分支。
- **能連外網**：`git clone` → `git checkout <tag>` → 建 venv → `pip install -e .`。正式機只裝執行依賴，不必裝 `[dev]`。
- **不能連外網**：在一台 aarch64、同樣 Python 版本的機器上 `pip download` 依賴（`jsonschema`、`rfc3339-validator` 及其相依套件），連同 repo 的打包檔一起帶上 Jetson，`pip install --no-index --find-links` 安裝。
- 裝完在 Jetson 上確認：

  ```bash
  radar-kg-build --check
  radar-kg-manifest --check
  radar-kg-level 來回行走 5.4
  radar-caregiver data/samples_demo/bathroom_morning.jsonl --place "1 號廁所" --json
  ```

- 建議在 Jetson 上另外裝 `[dev]` 跑一次完整的 `python -m pytest`：CI 只在 x86 上跑過，ARM 平台還沒驗證。跑完可以移除。

## 3. 在地模型

- 推論伺服器：llama.cpp server（CUDA 版）或 Ollama，兩者都有 OpenAI 相容 API，`radar-ask`、`radar-model-eval` 可以直接接（`--endpoint`）。
- **只聽 `127.0.0.1`**：`local_model.HttpChatModel` 只給本機或區網的模型伺服器用，不對外開放。
- 推論伺服器會不會記錄 prompt（裡面可能有讀者打的字），部署時要確認並關掉（`12_Demo預演.md` 問答）。
- 下載模型前記下開發者、base model、授權（`docs/adr/0003`）；先試 Llama-Breeze2 3B。中國開發或以其為基底的模型，`radar-model-eval` 會依名稱擋下。
- 照 `06` 第 4、4.1 節實測：
  - `radar-model-eval` 跑三份題組（`--event-questions`、`--questions`、改寫 `--show`）；
  - 事件提問的對錯題要是 0，正確率暫定 80% 以上；
  - `tegrastats` 看記憶體峰值與溫度，連續跑 1 小時以上，確認不會過熱降頻。
- 決策順序照 `06` 第 5 節：先找通過品質門檻的最小模型，再看記憶體有沒有 30% 餘裕，最後看速度。

## 4. 和 Dashboard 交換資料（要寫新程式）

第一階段由 Dashboard 提供 JSON，Jetson 暫時不直接收 MQTT（`docs/16`）。可行的做法：

| 做法 | 怎麼運作 | 適合 |
|---|---|---|
| (a) 檔案交換 | Dashboard 把 NDJSON 放到 Jetson 的固定目錄；排程呼叫 `radar-caregiver --json --from --to`，結果寫到另一個目錄給 Dashboard 取 | Demo 最快，不必新增服務 |
| (b) Jetson 定時去拉 | 照 `docs/16`「資料交換 API 草案」向 Dashboard 要資料，產生文字後送回 | 正式流程 |
| (c) Jetson 開一個小型 HTTP 服務 | Dashboard 呼叫 Jetson；即時提問（`radar-ask`）比較順 | 要即時互動時 |

由公司研發與 Dashboard 負責人決定。(b)、(c) 都是新程式，碰到單向門「個人資料的流向」（傳送長者資料），照 `REVIEW.md` 審查。不論哪一種，輸出檢查沒通過時 `radar-caregiver` 不輸出文字、結束碼 1，Dashboard 端要能處理。

## 5. 讓服務自動執行

- 用 systemd 管理：
  - `llama-server.service`：開機啟動、掛掉自動重啟；
  - `radar-agent.service` 加 `.timer`：依第 4 步的做法排程執行。
- **資源隔離**（`02_目標架構.md`「在地模型負載與分流」）：推論用 `Nice`、`CPUQuota`、`MemoryMax` 限制，不能拖慢 MQTT 接收或 radarLLM。
- 日誌用 journald 並限制保存天數；日誌不寫裝置 MAC、紀錄內容、讀者打的字。
- 防火牆只開 Dashboard 需要的埠；模型伺服器不對外。

## 6. 驗收與彩排

- 模擬資料從頭走一遍第 4 步的流程，`demo/flow_check.html` 列的情境都要走過。
- 換成遮罩後的實測資料再走一次（`radar-field-data mask`；實測資料不進 repo）。
- 量 `radar-ask` 從提問到回覆的總時間。
- 同時灌 MQTT 事件，確認接收延遲不受推論影響（`06` 第 4 節第 3 項）。
- Demo 前在 Jetson 上完整彩排一次（`11_Demo規劃.md`，2026-10-19 決定）。

## 7. 更新與回復

- 每個 tag 一個 `releases/<版本>` 資料夾；更新：拉新 tag → 跑第 2 步的檢查 → 切換 `current` → 重啟服務。
- 回復：把 `current` 切回上一版、重啟。
- 模型檔另外記版本；換模型要重跑第 3 步的題組。
- 知識圖譜未核准前，輸出標「僅供測試」是正常的（`kg/README.md`）。

## 之後（第 2、3 階段）

- Jetson 直接收 MQTT：payload 沒有時間戳，要由 Jetson 收到時補上（週一 2-1、A27）；斷線補傳（A13、A14）。
- 多台 Jetson 的分工、綁定與版本同步（B7～B9）。
- Neo4j 是否放 Jetson 本機（B3），會和模型搶記憶體（`06` 第 1 節）。

## 建議順序（以 11 月中 Demo 為目標）

1. 現在：在筆電跑完第 3 步的準確率題組（`06` 第 4.1 節）；和 Dashboard 負責人決定第 4 步的做法。
2. 拿到 Jetson 規格與存取權後：第 1、2、3 步，重量速度。
3. 第 4 步的做法定案後：寫交換資料的程式、第 5 步，然後第 6 步彩排。
