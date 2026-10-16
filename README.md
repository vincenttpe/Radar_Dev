# Radar_Agent

長者照護用的 mmWave 雷達姿態判斷與健康建議系統。雷達與裝置上的小型語言模型負責偵測與語音確認；後端依知識圖譜判斷，產出給居服員、醫師、家屬、長者本人與主管機關的建議與報告。跌倒警示走獨立的即時路徑，不經過知識圖譜與 LLM。

> 目前狀態：**原型階段**。本 repo 由舊雛形 Health-care-report-LLM 的 zip（2026-10-06 取得）重新整理而來，舊 repo 不再修改。新系統已有事件驗證與知識圖譜工具（`src/radar_agent/`），正式資料串接與評分規則要等會議確認（見 `docs/05_會議待詢問清單.md`）。
>
> 範圍（2026-10-08 確認）：現有系統已保存最近 7 天的雷達資訊（雲端 HTTPS，或邊緣裝置經 MQTT 送到 Jetson PC 的同一份），本 repo 只用其中的動作狀態 JSON；本 repo 負責用規則把這些資訊對應到知識圖譜的等級，再用在地模型依五種 Profile（居服員、醫師、家屬、長者本人、主管機關）輸出狀態、建議等文字訊息。雷達偵測、跌倒警示與資料保存都在現有系統；甲方週報（跌倒次數、活動量）由 Dashboard 處理（2026-10-12 決定，交接見 `docs/07_週報交接_Dashboard.md`），都不在本 repo 範圍。詳見 `docs/02_目標架構.md`。

## 目錄

| 路徑 | 內容 |
|---|---|
| `docs/` | 現況架構、目標架構、待確認事項、遷移紀錄、會議待詢問清單、Jetson 硬體評估、週報交接（給 Dashboard）、KPI 與查核點 |
| `kg/v1/` | 知識圖譜第 1 版：`graph.cypher`（建圖腳本）、`metrics.json`（同內容的結構化版本）、`bridge.cypher` |
| `kg/source/` | 知識圖譜原始文件（`graph.docx`、`standard_suggestion.xlsx`） |
| `kg/dumps/` | Neo4j 資料庫備份（2026-05-10） |
| `data/labels/` | 舊雛形用的動作描述標註資料 |
| `data/samples/` | 舊雛形的測試輸入（6 個 JSON，共 64 筆） |
| `data/samples_fall/` | 2026-10-08 取得的動作描述 JSON 樣本，多 `fall` 欄位 |
| `data/samples_device_event/` | 裝置動作事件：2026-10-08 收到的一筆樣本，與 2026-10-12 取得的 MQTT 訊息規格（13 種訊息） |
| `schemas/` | 雷達事件格式草案（**未經確認**） |
| `legacy/prototype/` | 舊雛形程式，僅供參考與比對，不再開發 |
| `src/radar_agent/` | 新系統的程式。目前有事件驗證（`event_validation.py`）、知識圖譜發布清單工具（`kg_manifest.py`）、門檻解析與等級判定（`kg_source.py`、`kg_rules.py`）與 PR 單向門檢查（`pr_gate.py`） |
| `REVIEW.md` | 審查規範：單向門清單、規範與需求兩軸的檢查重點；PR 範本在 `.github/pull_request_template.md`，CI（`pr-gate.yml`）依變更的檔案檢查單向門勾選 |
| `.claude/skills/` | Claude 用的專案 skill：`kg-check`（知識圖譜一致性檢查） |
| `tests/` | 資料檔的一致性測試與上述程式的測試，不載入模型、不連資料庫 |

## 從哪裡開始讀

1. `docs/01_現況架構.md`：舊雛形怎麼運作、有哪些問題。
2. `docs/02_目標架構.md`：新系統的方向。
3. `docs/03_待確認事項.md`：開始寫程式前要先確認的事。
4. `docs/08_KPI與查核點.md`：結案驗收的 KPI、查核點時程，以及規格與本 repo 現況的落差。
5. `docs/09_換算規則草稿.md`：雷達資料能不能換算成知識圖譜五項指標（目前都不能）與若由雷達量測時的規則草稿。
6. `docs/11_Demo規劃.md`：Demo 以計畫書為主軸要展示什麼、本 repo 對應到哪裡、還缺什麼。
   `docs/12_Demo預演.md`：講稿與時間、問答準備、當天檢查清單。
   `docs/13_內部演示流程.md`：對團隊的演示流程、從現在的資料換到 Jetson 輸出要改什麼、10/19 確認 Dashboard 架構的題目。
7. `docs/10_會議決策單_2026-10-16.md`：目前卡住開發的決定，每題附選項與影響，開會時逐題勾選。
8. `REVIEW.md`：送 PR 前要知道的審查規範，特別是第 2 節的單向門。

## 安裝與使用

需要 Python 3.11 以上。在 repo 根目錄：

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows：.venv\Scripts\activate
pip install -e ".[dev]"          # 必須是可編輯安裝（-e），程式會讀 repo 裡的 schemas/、kg/
```

安裝後，在任何目錄都有這些指令：

```bash
radar-kg-build --check                                                            # 檢查 metrics.json、thresholds.json 是否與 graph.cypher 一致
radar-kg-manifest --check                                                         # 檢查知識圖譜發布清單
radar-kg-level 來回行走 5.4                                                       # 量測值 → 知識圖譜等級與建議（--list 列出門檻）
radar-caregiver 事件.jsonl --place "1 號廁所"                                     # 一台裝置的事件 → 居服員看的場域摘要（輸出檢查沒過就不輸出）
radar-field-data inspect 實測.json                                                 # 實測資料的格式、時間範圍、事件數與每小時分布（不印出 MAC）
radar-field-data mask 實測.json --out-dir outputs/field                            # 裝置識別換成 FIELD-01…，每台一個檔案；只能寫到 outputs/
```

`git pull` 之後如果 `pyproject.toml` 有變動，再執行一次 `pip install -e ".[dev]"`。

送 PR 前，可以先在本機檢查 PR 說明的單向門與驗證證據勾選（CI 的 pr-gate 跑的是同一支程式）：

```bash
git -c core.quotePath=false diff --name-only main...HEAD > changed.txt
python -m radar_agent.pr_gate --body PR說明.md --changed changed.txt
```

## 測試

```bash
python -m pytest
```

用 `python -m pytest` 而不是 `pytest`，可以確保用的是目前啟動的虛擬環境；機器上如果另有一份 `pytest`，直接打 `pytest` 可能抓到那一份而缺少 `pytest-cov` 等套件。

測試涵蓋：

- 知識圖譜：`kg/v1/` 各檔案彼此一致；門檻沒有未登記的缺口或重疊；`kg/releases.json` 的雜湊與實際檔案相符。
- 事件格式：`schemas/event.v0.draft.json` 的必填與拒收行為、日期格式、時間先後（`radar_agent.event_validation`）。
- 資料：`data/` 的格式，以及文件引用的數字；舊雛形 `--input` 的路徑解析。

改這些檔案時先跑一次。改了 `kg/v1/` 要先更新 `kg/releases.json`，指令見 `kg/README.md`。

### 品質檢查

CI 在每個 PR 與 push 到 `main` 時，用 Python 3.11 與 3.12 各跑一次下面三項，任何一項失敗 PR 就是紅的。本機先跑：

```bash
ruff check src tests     # 風格與常見錯誤（規則明確列在 pyproject.toml，不依賴 ruff 預設值）
mypy                     # 型別檢查，src 用嚴格模式
python -m pytest --cov  # 測試；覆蓋率低於 95% 會失敗（含分支覆蓋）
```

設定都在 `pyproject.toml`。沒有時區的 `datetime` 與全形標點的規則是刻意關掉的，理由寫在設定旁邊；不要為了讓檢查通過而逐行加 `# noqa`。ruff 目前不做格式化檢查（`ruff format`），原因是現有程式有不少長行，要一次性重排再開。

## 設定

密碼與金鑰一律用環境變數，不寫進程式碼。複製 `.env.example` 為 `.env` 後填入；`.env` 已列在 `.gitignore`。
