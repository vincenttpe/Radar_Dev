# Jetson 部署流程（草案，2026-10-26；2026-10-27 更新）

把本 repo 部署到現場 Jetson PC 的步驟。Demo 機台的規格與網路見 `docs/05` B1、B5（Orin NX 16GB，能連外網）。型號怎麼選見 `docs/06_Jetson硬體評估.md`；第一階段和 Dashboard 怎麼交換資料見 `docs/16_Dashboard對接說明.md`。

標「待核對」的是還沒在實機上確認的事，拿到 Jetson 後要先驗證；標「草案」的是這份文件提出的做法，還沒有決策紀錄，待 B11（誰維護、怎麼更新）確認後再定。

## 先知道的五件事

1. **Python 版本**：本 repo 要求 Python 3.11 以上（`pyproject.toml`）。Demo 機台內建 Python 3.10.12（2026-10-27 實機確認），要另外裝 3.11 以上，做法見第 1 步。**系統的 `python3` 不要升級或替換**（也不要用 `update-alternatives` 改指向）：apt 與機台上其他程式依賴它。
2. **只能用原始碼加可編輯安裝**：`kg_rules`、`kg_source`、`kg_manifest`、`event_validation` 用 `Path(__file__).parents[2]` 讀 repo 裡的 `kg/` 與 `schemas/`（`pyproject.toml` 也有註明）。打包成 wheel 安裝，執行時會找不到知識圖譜。帶上 Jetson 的一律是原始碼（git checkout，或某個 tag 的 tar 檔）。
3. **Dashboard 現在的實測資料讀不懂**：Dashboard 下載的實測資料是 Radar60FL 格式，本 repo 只讀得懂 radar10，遇到 Radar60FL 只會寫「沒有收到資料，無法判斷」（`docs/16`；會議清單 A45～A50）。在格式問題解決前，實測資料走完流程也只會得到這句話，驗收要用模擬資料，或之前取得的 radar10 裝置事件（10/09～10/13 一台裝置，A10）。
4. **和 Dashboard 交換資料的程式還沒有**：目前只有命令列（`radar-caregiver --json`、`radar-ask --json`）。方向已定：由 Jetson 向 Dashboard 要資料、產生文字後傳回（`docs/16`「第一階段的流程」，2026-10-23）；網址、認證、參數、文字傳回哪裡、多久一次都還沒約定（週一清單 4-1、4-2；會議清單 B4、B10）。
5. **跌倒即時警示不經過本 repo**（`docs/adr/0006`）：本 repo 部署失敗不影響警示，可以分階段上線。

## 0. 開始前要有答案

| 題號 | 問題 | 卡住哪一步 |
|---|---|---|
| 週一清單 3-1（B1） | Jetson 型號、記憶體、JetPack 版本、可用磁碟（點雲在 Jetson 上預設保留一個月，要一起算，A24） | 模型大小、Python 怎麼裝、磁碟夠不夠 |
| 週一清單 3-2（B2） | Jetson 上還跑什麼（MQTT broker、radarLLM、音訊檔目錄） | 資源分配、要隔離的目錄 |
| 週一清單 3-3（B5） | 能不能連外網 | 線上安裝，或先在別台下載好再離線帶上去 |
| 週一清單 3-4（F2） | 在地模型選哪個；授權能否商用、能否在機構現場部署 | 第 3 步 |
| 週一清單 4-1、4-2（B4） | Dashboard 的 API：網址、認證、參數、文字傳回哪裡 | 第 4 步 |
| B10 | 報告多久產生一次、幾點前要好；提問是不是即時 | 排程時間；`docs/06` 第 5 節「批次時段內只用一半時間跑完」 |
| B11 | 誰維護、能不能遠端登入與更新、程式失敗通知誰、日誌存哪 | 第 5、7 步 |
| B12 | 磁碟加密、MQTT 與 HTTPS 的加密與認證、實體上誰碰得到 | 資安設定 |
| A16、A17 | 客戶要求不同步到雲端時，Jetson 上的資料是唯一一份：要不要備份、備份到哪；「不同步」以誰為單位 | 第 1 步的資料目錄與備份 |

B1、B5 和 B2 的一部分可以自己查：拿到 Jetson 的登入帳號後執行 `bash scripts/jetson_check.sh`。它只讀，不安裝、不改設定、不用 sudo，也不印出 IP、MAC、主機名稱與使用者名稱，會列出型號、JetPack、記憶體、磁碟、電源模式、Python 版本、正在執行的服務與聽的埠、能不能連外網，最後附摘要。還沒把 repo 放上 Jetson 時，只複製這一個檔過去執行即可；不想連外時加 `--offline`。

## 1. 準備 Jetson

- 刷好 JetPack，記下版本與電源模式（`docs/06` 第 4 節第 5 項：結果才能重現、比較）。
- 裝 Python 3.11 以上與 `git`。用 `uv` 另外放一份 3.11，裝在使用者自己的目錄，不需要 sudo，也不動系統的 3.10：

  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  source ~/.local/bin/env            # 舊版安裝程式沒有這個檔時，改 export PATH="$HOME/.local/bin:$PATH"
  uv python install 3.11
  uv python find 3.11                # 印出路徑就是裝好了
  ```

  用 deadsnakes（`sudo apt install python3.11 python3.11-venv`）也可以，但要動系統套件，先問過維運的人（B11）；`python3.11-venv` 一定要裝，否則建不了 venv。程式改由專用帳號執行時，uv 與 Python 也要裝在那個帳號下（或用 `UV_PYTHON_INSTALL_DIR` 放到共用目錄，待核對）。
- 開專用帳號（例如 `radar`），程式不用 root 跑。
- 用檔案權限讓這個帳號讀不到音訊檔目錄（`docs/adr/0005`）。
- 目錄（草案）：
  - `/opt/radar_agent/releases/<版本>`：每個版本一份原始碼，各自有自己的 venv（`<版本>/.venv`）；
  - `/opt/radar_agent/current`：連到使用中的版本；
  - `/var/lib/radar_agent`：紀錄與輸出，屬個人資料（B12）；不同步到雲端的客戶，這裡是唯一一份資料，備份方式待 A16；放進來的紀錄檔與產生的輸出要留多久、誰來清還沒決定（歷史資料要留多久見 E8）；
  - `/opt/models`：模型檔。

## 2. 安裝程式

- 部署用的版本在 main 打 tag（草案：只部署 tag，不部署任意分支）。
- 每個版本放進自己的 `releases/<版本>`，在那個資料夾裡建 venv、`pip install -e .`。可編輯安裝記下的是安裝時的實際路徑，所以**不能共用 venv，也不能只換原始碼不重裝**。正式機只裝執行依賴，不必裝 `[dev]`。
- **能連外網**（Demo 機台是這種）：`git clone` → `git checkout <tag>` → `uv venv --seed --python 3.11 .venv`（`--seed` 會把 pip 一起裝進 venv）→ `source .venv/bin/activate` → `pip install -e .`（要在 Jetson 上跑測試時改 `pip install -e ".[dev]"`）。
- **不能連外網**：在一台 aarch64、同樣 Python 版本、可上網的機器上，把下面這些都下載好，連同 tag 的原始碼（tar 檔或 git bundle）一起帶上 Jetson：
  - 執行依賴：`jsonschema`、`rfc3339-validator` 及其相依套件；
  - 建置可編輯安裝需要的 `setuptools>=68`（`pyproject.toml` 的 `[build-system]`）與 `wheel`；
  - 要在 Jetson 上跑測試的話，再加 `[dev]` 的依賴。

  安裝時用 `pip install --no-index --find-links <下載目錄> -e .`（待核對：實際跑一次確認沒有漏掉的套件）。
- 裝完，在該版本的 repo 根目錄、用**該版本自己的 venv** 確認（先 `source .venv/bin/activate`；下面的資料路徑是相對 repo 根目錄）：

  ```bash
  radar-kg-build --check
  radar-kg-manifest --check
  radar-kg-level 來回行走 5.4
  radar-caregiver data/samples_demo/bathroom_morning.jsonl --place "1 號廁所" --json
  ```

- 建議在 Jetson 上另外裝 `[dev]`、跑一次完整的 `python -m pytest`：CI 只在 x86 上跑過，ARM 平台還沒驗證。

## 3. 在地模型

- 推論伺服器：llama.cpp server（CUDA 版）或 Ollama，兩者都有 OpenAI 相容 API，`radar-ask`、`radar-model-eval` 可以直接接（`--endpoint`）。
- **Demo 用 Ollama，不需要 Docker**（2026-10-27 在 Demo 機台確認：剛裝好時是 100% CPU，加上下方的 libcuda 設定後才用到 GPU）。安裝程式要 sudo，會建立 `ollama` 帳號與 systemd 服務，先問過維運的人（B11）：

  ```bash
  curl -fsSL https://ollama.com/install.sh | sh
  ollama --version                     # 記下版本（docs/06 第 4 節第 5 項）；Demo 機台是 0.40.11
  ollama run llama3.2:3b --verbose "用一句話介紹你自己"
  ollama ps                            # PROCESSOR 要是 100% GPU
  # repo 裝好後（第 2 步）量採用率、延遲與速度：
  radar-model-eval --endpoint http://127.0.0.1:11434/v1 --model llama3.2:3b
  ```

  Ollama 預設只聽 `127.0.0.1:11434`，不要改成對外。Gemma 4 E4B 在 Ollama 的名稱是 `gemma4:e4b`（Demo 機台已拉下並在 GPU 上跑過）。Llama 3.2 與 Gemma 4 的授權能不能商用、能不能在機構現場部署仍待 F2。
- **`ollama ps` 顯示 100% CPU 時**，照這個順序查：
  1. `journalctl -u ollama | grep -A8 "discovering available GPUs"`：看 `inference compute` 那行是 `library=cpu` 還是 `library=CUDA`；
  2. 原因要開除錯紀錄才看得到：在服務的覆寫設定（`/etc/systemd/system/ollama.service.d/` 底下的 `.conf`，寫法同下方的 `printf`）加一行 `Environment="OLLAMA_DEBUG=1"`，`daemon-reload` 後重啟；日誌出現 `cuInit failed: 100` 代表 CUDA 找不到裝置；查完把這行拿掉；
  3. 確認不是權限或驅動：`groups ollama` 要有 `video`、`render`，`lsmod | grep nvgpu` 要有東西；
  4. `sudo dmesg | grep NVRM`：出現 `API mismatch`（使用者端元件和核心驅動版本不同）時，代表系統裡混有其他版本的 NVIDIA 元件，程式載入了錯的 `libcuda`。

  Demo 機台是第 4 種：系統裡有 580.126.09 版的元件，核心驅動是 JetPack 6 的 540.4.0（[#25](https://github.com/vincenttpe/Radar_Dev/issues/25)）。解法是讓 Ollama 的服務優先用 L4T 自帶的 `libcuda`：

  ```bash
  sudo mkdir -p /etc/systemd/system/ollama.service.d
  printf '[Service]\nEnvironment="LD_LIBRARY_PATH=/usr/lib/aarch64-linux-gnu/nvidia"\n' | sudo tee /etc/systemd/system/ollama.service.d/l4t-cuda.conf
  sudo systemctl daemon-reload && sudo systemctl restart ollama
  journalctl -u ollama --since "1 min ago" --no-pager | grep "inference compute"   # 要看到 library=CUDA、description=Orin
  ```

  覆寫設定的檔名可以自取，Demo 機台上用的是 `igpu.conf`，內容相同。重啟後日誌可能要等幾秒才寫出。  根本的解法（移除不屬於 JetPack 的元件）由維運決定，見 #25；決定前不要自己移除套件。
- **Ollama 用不了 GPU 時的備案：llama.cpp**。NVIDIA Jetson AI Lab 對 Orin NX 16GB 給的是 llama.cpp 容器（`ghcr.io/nvidia-ai-iot/llama_cpp:latest-jetson-orin`）＋ `llama-server` 跑 GGUF 量化檔（[教學](https://www.jetson-ai-lab.com/tutorials/gemma4-on-jetson/)，標示 JetPack 7.2.1；Demo 機台是 JetPack 6.2 系列，能不能跑待核對）；也可以不用 Docker、在 Jetson 上自己編譯 llama.cpp。用容器要 Docker 與 sudo（或 docker 群組，等於 root 權限），先問過維運的人（B11、B12）。照教學的指令改三處：
  - 加 `--host 127.0.0.1`（容器用 `--network host` 時，伺服器會直接聽在機器的網卡上）；
  - `--ctx-size` 調小：教學給 36864，我們的 prompt 短，調小可以省下記憶體（實際要多少待實測）；
  - 映像檔與模型檔記下版本或雜湊，不用 `latest` 當紀錄（`docs/06` 第 4 節第 5 項）。

  量化先用 Q4_K_M（E4B 約 5.3GB，`docs/adr/0003`）。啟動後接 `radar-model-eval --endpoint http://127.0.0.1:<埠號>/v1 --model <模型名稱>`。

  注意：系統裡的 `libcuda` 版本混用（#25）推測也會影響容器與自己編譯的 llama.cpp（待核對）。
- **vLLM 先不用**：網路上常見的 vLLM 範例（`vllm/vllm-openai:latest` 加 NVFP4 量化檔）不能直接照抄：
  - NVFP4 是 4-bit 浮點格式，Jetson AI Lab 只在 Thor 列 vLLM＋NVFP4；Orin 是 Ampere 架構，推測沒有 FP4 的硬體加速（待核對）；
  - `vllm/vllm-openai` 是一般的映像檔，不是 Jetson 專用版，能不能在 Orin 上跑待核對；
  - vLLM 預設聽所有網卡，要加 `--host 127.0.0.1`；
  - `--gpu-memory-utilization 0.7` 會在啟動時先佔走約 70% 的記憶體，和 MQTT、radarLLM（B2）搶空間，也違反 `docs/06` 第 5 節的 30% 餘裕。

  Ollama 與 llama.cpp 量起來速度都不夠時再評估。
- **思維鏈（thinking）預設關閉**：我們的工作是選題號、照範本改寫，不需要深度推理。開了以後：
  - 回覆變慢；
  - 思考內容會吃掉 `local_model.HttpChatModel` 的 `max_tokens`（512），答案可能被截斷；
  - 思考內容混進回覆時，輸出檢查可能因為多出來的文字退回範本，採用率變低（待核對）。

  `HttpChatModel` 目前也不會送 `enable_thinking` 這類參數；要比較開與不開，得先改程式。Demo 機台上 `ollama run gemma4:e4b` 問三句話的問題，回了 792 個 token，`--verbose` 顯示總時間 2 分 15 秒（當時還在用 CPU），推測是預設開了思維鏈；`ollama run` 可以加 `--think=false` 關掉（Demo 機台上還沒驗證），經 API 呼叫時要怎麼關待核對。
- **只聽 `127.0.0.1`**：`local_model.HttpChatModel` 只給本機或區網的模型伺服器用，不對外開放。
- 推論伺服器會不會記錄 prompt（裡面可能有讀者打的字），部署時要確認並關掉（`docs/12_Demo預演.md` 問答）。
- 下載模型前記下開發者、base model、授權（`docs/adr/0003`）；建議先試 Gemma 4 E4B（待確認），再拿 Gemma-3-TAIDE-12B-Chat 比繁體中文品質（Llama-Breeze2 已排除）。中國開發、以其為基底或含有中國開發部分的模型，`radar-model-eval` 會依名稱擋下。
- 實測照 `docs/06` 第 4 節與 4.1 節做：題組、通過門檻、要量的項目（速度、記憶體峰值、連續 1 小時以上的溫度與降頻）、要記錄的 JetPack 版本、電源模式、量化方式與推論框架版本，都以那兩節為準。
- 決策順序照 `docs/06` 第 5 節：先找通過品質門檻的最小模型，再看記憶體有沒有 30% 餘裕，最後看速度。

## 4. 和 Dashboard 交換資料（要寫新程式）

方向已定：Jetson 向 Dashboard 要資料、產生文字後傳回（`docs/16`）。細節還沒約定前，依用途分成三種：

| 做法 | 怎麼運作 | 用在 |
|---|---|---|
| 過渡：手動或檔案交換 | 照 `docs/16`「資料交換 API 草案」的手動方式：Dashboard 的 Logs 頁設定時間範圍、打開 heartbeat 與 topic 後匯出 NDJSON（沒有心跳，報告會把整段判成斷線或無法判斷），放到 Jetson 的固定目錄；排程或手動執行 `radar-caregiver --json --from --to`，結果寫到另一個目錄給 Dashboard 取 | API 約定前、Demo 彩排 |
| 正式：Jetson 定時去拉 | 照 `docs/16` 的 API 草案向 Dashboard 要資料，產生文字後送回 | API 約定後 |
| 提問：Jetson 開一個小型 HTTP 服務 | Dashboard 呼叫 Jetson 跑 `radar-ask`；提問要即時回覆時才需要（B10；`docs/16`「還沒定的」：提問是即時呼叫還是同一條路） | 有即時提問時 |

注意：

- 不論哪一種，交換資料的程式都會讀取、搬移長者的資料，屬單向門「個人資料的流向」，照 `REVIEW.md` 審查。
- 結束碼：
  - `radar-caregiver`：輸出檢查沒通過是 1，不輸出文字；讀檔失敗或參數錯誤（例如時間看不懂）是 2。不能把空輸出當成「沒有事件」。
  - `radar-ask`：輸出檢查沒通過也是 1，但仍輸出 JSON，`answer` 是 `null`、原因在 `rejected`（`docs/16`）；讀檔失敗或模型不能用是 2，不輸出 JSON。
  - 交換資料的程式與 Dashboard 端都要能處理這些情況。
- 實測資料目前是 Radar60FL，讀不懂（見「先知道的五件事」第 3 點）。

## 5. 讓服務自動執行（草案）

- 用 systemd 管理：
  - 推論伺服器：llama.cpp 用自己寫的 `llama-server.service`；Ollama 用它安裝時附的服務。開機啟動、掛掉自動重啟。
  - 本 repo：過渡與正式做法是排程工作，用 `radar-agent.service` 加 `.timer`，時間依 B10；提問服務是常駐服務，不用 `.timer`。
  - `ExecStart` 寫 `/opt/radar_agent/current/.venv/bin/...`，換版本時切換 `current` 後重啟就會用到新版本。
- 資源隔離（`docs/02_目標架構.md`「在地模型負載與分流」）：推論用 `Nice`、`CPUQuota`、`MemoryMax` 限制，不能拖慢 MQTT 接收或 radarLLM；數值待實測後再定。
- 日誌用 journald 並限制保存天數；日誌不寫裝置 MAC、紀錄內容、讀者打的字。
- 防火牆只開 Dashboard 需要的埠；模型伺服器不對外。

## 6. 驗收與彩排

- 模擬資料從頭走一遍第 4 步的流程，`demo/flow_check.html` 列的情境都要走過。
- 用 radar10 的實測資料（10/09～10/13 的裝置事件，A10；先用 `radar-field-data mask` 遮罩；實測資料不進 repo）再走一次。這份資料大多是無人，沒有跌倒與久待，只能確認在線與造訪；跌倒、久待的情境仍靠模擬資料。Radar60FL 的格式問題解決前，用它走只會得到「沒有收到資料，無法判斷」。
- 量 `radar-ask` 從提問到回覆的總時間。
- 同時灌 MQTT 事件，確認接收延遲不受推論影響（`docs/06` 第 4 節第 3 項）。
- 部署完成、確認在 Jetson 上執行後，才請 Dashboard 打開「在 Jetson 本機執行、提問與資料不出場域」的開關（`docs/16`，2026-10-19 決定）。
- Demo 前在 Jetson 上完整彩排一次（`docs/11_Demo規劃.md`，2026-10-19 決定）。

## 7. 更新與回復（草案）

- 更新：新的 tag 放進新的 `releases/<版本>`，照第 2 步建 venv、安裝、跑檢查 → 切換 `current` → 重啟服務。
- 回復：把 `current` 切回上一版、重啟。舊版本的資料夾與 venv 保留到確認新版穩定為止。
- 模型檔另外記版本；換模型要重跑第 3 步的題組。
- 知識圖譜未核准前，輸出標「僅供測試」是正常的（`kg/README.md`）。

## 個人資料

- 本 repo 給 Dashboard 的 JSON 只寫地點名稱，不含裝置識別碼（`caregiver.report_data`）；地點名稱不放長者姓名（`docs/16`）。
- 輸入的紀錄檔含裝置 MAC，一戶一台時等於可識別個人（`docs/02`）。留在 `/var/lib/radar_agent`；給研究或第三方時怎麼處理待 F10，在那之前先用 `radar-field-data mask` 換成代號。

## 之後（第 2、3 階段）

- Jetson 直接收 MQTT：payload 沒有時間戳，要由 Jetson 收到時補上（週一清單 2-1、A27）；斷線補傳（A13、A14）。
- 多台 Jetson 的分工、綁定與版本同步（B7～B9）。
- Neo4j 是否放 Jetson 本機（B3），會和模型搶記憶體（`docs/06` 第 1 節）。

## 建議順序（以 11 月中 Demo 為目標）

Demo 機台已定（B1）、能連外網（B5），依下面四個階段進行（2026-10-27，使用者在對話中決定）：

1. **安裝 Python 3.11**：照第 1 步用 `uv` 另外裝一份，系統的 3.10 不動。完成：`uv python find 3.11` 印出路徑。
2. **裝推論伺服器與 Llama 模型**：照第 3 步裝 Ollama、拉一個 3B 模型，確認能回話，用 `tegrastats` 看記憶體與 GPU。模型品質要等第 3 階段用 `radar-model-eval` 量。
3. **把 repo 放上 Jetson**：照第 2 步安裝（先在使用者家目錄試裝；`/opt` 目錄與只部署 tag 仍是草案，見 issue [#18](https://github.com/vincenttpe/Radar_Dev/issues/18)），裝 `[dev]`、跑確認指令、完整 `pytest` 與 `scripts/jetson_check.sh`；接上第 2 階段的模型跑 `radar-model-eval`，並照 `docs/06` 第 4 節連續跑 1 小時以上看溫度與降頻（無風扇機種）。
4. **和雲端 Dashboard 配合**：API 約定（週一清單 4-1、4-2，B4）前，先照第 4 步的過渡做法手動交換檔案走一次；Radar60FL 的格式問題（A45～A50）解決前，用 Demo 模擬資料。API 約定後再寫交換資料的程式、第 5 步，然後第 6 步彩排。

同時進行：在筆電跑完 `docs/06` 第 4.1 節的準確率題組，並追 Radar60FL 的格式。
