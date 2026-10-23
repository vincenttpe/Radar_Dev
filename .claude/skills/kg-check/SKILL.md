---
name: kg-check
description: 檢查知識圖譜（kg/）的衍生檔、雜湊與核准狀態是否一致，以及相關測試。改過 kg/、kg_source.py、kg_manifest.py 或 kg_rules.py，commit 或 PR 含這些變更，CI 的 radar-kg-build／radar-kg-manifest 或知識圖譜測試失敗，或使用者要「檢查知識圖譜」時使用。
---

# 知識圖譜一致性檢查

規則的正本是 `kg/README.md`「更新規則」一節；這個 skill 是照它執行的檢查流程。

依賴：雲端工作階段通常已由 SessionStart hook 裝好，但工作階段不是從 repo 根目錄開始時 hook 不會跑。下面的指令出現 `command not found`、`No module named pytest` 或 `No module named radar_agent`，先在 repo 根目錄執行 `python -m pip install -r requirements-dev.txt`（等同 `pip install -e ".[dev]"`）再重跑；裝好後仍找不到 `radar-kg-*`，表示 pip 的 scripts 目錄不在 PATH，逐條換成下面的寫法，**參數照抄、`--check` 不能省**：

| 指令 | 替代寫法 |
|---|---|
| `radar-kg-build --check` | `python -m radar_agent.kg_source --check` |
| `radar-kg-manifest --check` | `python -m radar_agent.kg_manifest --check` |
| `radar-kg-level 來回行走 5.4` | `python -m radar_agent.kg_rules 來回行走 5.4` |

## 步驟

依序執行，**都只讀不寫**：

```bash
radar-kg-build --check       # metrics.json、thresholds.json 是否與 graph.cypher 一致
radar-kg-manifest --check    # kg/releases.json 的雜湊、已核准版本有沒有被改
radar-kg-level 來回行走 5.4   # 規則引擎能讀 v1 並判出等級（kg/README.md 沒列這步，為了與 CI tests.yml 一致而加）
python -m pytest tests/test_kg*.py tests/test_thresholds.py -q   # 含 test_kg_demo：Demo 頁內嵌知識圖譜資料
```

四項都通過就回報「一致」，並附上 `git status --short kg/` 的結果，讓使用者知道有哪些 kg 檔案還沒 commit。

## 失敗時怎麼處理

| 訊息 | 原因 | 處理 |
|---|---|---|
| `以下檔案與 graph.cypher 不一致，請執行 radar-kg-build` | 改了 `graph.cypher` 沒重新產生，或手改了衍生檔 | 先看 `git diff kg/v1/`。改 `graph.cypher` 是使用者本意 → 執行 `radar-kg-build` 再 `radar-kg-manifest`。手改了 `metrics.json`／`thresholds.json` → 衍生檔不能手改，改動要搬回 `graph.cypher`（單位與精度在 `src/radar_agent/kg_source.py` 的 `METRIC_CONFIG`），先問使用者再動。 |
| `kg/releases.json 與實際檔案不一致；請執行 radar-kg-manifest` | 檔案內容變了，清單雜湊沒更新 | 確認內容變動是有意的之後執行 `radar-kg-manifest`。 |
| `已核准的版本內容被改過：…` | 動到已核准（`approved`）版本的檔案 | **不要更新清單、不要改 approval**。核准綁的是核准時的內容：還原那些檔案，或建立新版本（例如 `kg/v1.1/`）重新送審。由使用者決定走哪一條。 |
| `tests/test_thresholds.py` 失敗 | 門檻的缺口或重疊有變化 | 已知問題（心肺耐力 100~111、步行速度 0.5／0.8／1.0 等）是測試刻意固定的，等審核人決定（會議清單 E5）。回報是哪一個缺口或重疊被新增或修掉，不要為了讓測試通過去改測試或門檻。 |
| `test_committed_page_is_exactly_what_kg_build_produces` 失敗（`tests/test_kg_demo.py`） | 知識圖譜內容變了，`demo/kg_demo.html` 內嵌的資料還是舊的 | 確認知識圖譜的變動是本意後，執行 `python demo/kg_build.py` 重新產生 Demo 頁，一起 commit。 |
| `radar-kg-level 來回行走 5.4` 失敗（包括它印出的 `錯誤：…`，例如雜湊不符），或沒有輸出等級 | 規則引擎讀不到或解析不了 `kg/v1/` 的 `thresholds.json`、`metrics.json`，或來回行走的門檻被改到 5.4 秒落在缺口 | 先跑完前兩項 `--check`，衍生檔不一致就照上面兩列處理；一致卻仍失敗，把錯誤原文與 `git diff kg/ src/radar_agent/kg_rules.py` 給使用者，不要為了通過而改門檻。 |
| `radar-kg-build --check` 印出 `錯誤：…`（結束碼 2） | `graph.cypher` 解析失敗或等級結構有誤 | 把錯誤原文與相關段落給使用者，修 `graph.cypher`。 |

## 必須遵守

- 知識圖譜內容與醫療建議相關，**不做全自動更新**。`radar-kg-build`、`radar-kg-manifest`（不加 `--check`）、它們的替代寫法 `python -m radar_agent.kg_source`、`python -m radar_agent.kg_manifest`（同樣不加 `--check`）與 `python demo/kg_build.py` 會寫檔，只在使用者確認內容變動是本意後才執行。
- 不改 `releases.json` 的 `approval`（核准狀態、審核人、日期）；那是審核人的事。
- 不手改 `metrics.json`、`thresholds.json`、`releases.json` 的雜湊。
