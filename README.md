# Radar_Agent

長者照護用的 mmWave 雷達姿態判斷與健康建議系統。雷達與裝置上的小型語言模型負責偵測與語音確認；後端依知識圖譜判斷，產出給居服員、醫師、家屬、長者本人與主管機關的建議與報告。跌倒警示走獨立的即時路徑，不經過知識圖譜與 LLM。

> 目前狀態：**整理階段**。本 repo 由舊雛形 Health-care-report-LLM 的 zip（2026-10-06 取得）重新整理而來，舊 repo 不再修改。新系統的程式碼尚未開始。
>
> 範圍（2026-10-08 確認）：現有系統已保存最近 7 天的雷達資訊（雲端 HTTPS，或邊緣裝置經 MQTT 送到 Jetson PC 的同一份），本 repo 只用其中的動作狀態 JSON；本 repo 負責用規則把這些資訊對應到知識圖譜的等級，再用在地模型依五種 Profile（居服員、醫師、家屬、長者本人、主管機關）輸出狀態、建議等文字訊息。雷達偵測、跌倒警示與資料保存都在現有系統，不在本 repo 範圍。詳見 `docs/02_目標架構.md`。

## 目錄

| 路徑 | 內容 |
|---|---|
| `docs/` | 現況架構、目標架構、待確認事項、遷移紀錄、會議待詢問清單 |
| `kg/v1/` | 知識圖譜第 1 版：`graph.cypher`（建圖腳本）、`metrics.json`（同內容的結構化版本）、`bridge.cypher` |
| `kg/source/` | 知識圖譜原始文件（`graph.docx`、`standard_suggestion.xlsx`） |
| `kg/dumps/` | Neo4j 資料庫備份（2026-05-10） |
| `data/labels/` | 舊雛形用的動作描述標註資料 |
| `data/samples/` | 舊雛形的測試輸入（6 個 JSON，共 64 筆） |
| `data/samples_fall/` | 2026-10-08 取得的動作描述 JSON 樣本，多 `fall` 欄位 |
| `data/samples_device_event/` | 2026-10-08 取得的裝置簡易動作事件樣本 |
| `schemas/` | 雷達事件格式草案（**未經確認**） |
| `legacy/prototype/` | 舊雛形程式，僅供參考與比對，不再開發 |
| `tests/` | 資料檔的一致性測試（知識圖譜、事件格式、標註、樣本），不載入模型、不連資料庫 |

## 從哪裡開始讀

1. `docs/01_現況架構.md`：舊雛形怎麼運作、有哪些問題。
2. `docs/02_目標架構.md`：新系統的方向。
3. `docs/03_待確認事項.md`：開始寫程式前要先確認的事。

## 測試

```bash
pip install -r requirements-dev.txt
pytest
```

目前的測試只檢查 repo 內的資料檔：`kg/v1/` 三個檔案彼此一致、`schemas/event.v0.draft.json` 的必填與拒收行為、`data/` 的格式與文件引用的數字、舊雛形 `--input` 的路徑解析。改這些檔案時先跑一次。新系統的程式開始後，測試會跟著程式加。

## 設定

密碼與金鑰一律用環境變數，不寫進程式碼。複製 `.env.example` 為 `.env` 後填入；`.env` 已列在 `.gitignore`。
