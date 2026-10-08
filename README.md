# Radar_Agent

長者照護用的 mmWave 雷達姿態判斷與健康建議系統。雷達與裝置上的小型語言模型負責偵測與語音確認；後端依知識圖譜判斷，產出給居服員、醫師、家屬、長者本人與主管機關的建議與報告。跌倒警示走獨立的即時路徑，不經過知識圖譜與 LLM。

> 目前狀態：**整理階段**。本 repo 由舊雛形 Health-care-report-LLM 的 zip（2026-10-06 取得）重新整理而來，舊 repo 不再修改。新系統的程式碼尚未開始，要先和雷達、Dashboard 兩邊確認事件格式與現況架構。

## 目錄

| 路徑 | 內容 |
|---|---|
| `docs/` | 現況架構、目標架構、待確認事項、遷移紀錄 |
| `kg/v1/` | 知識圖譜第 1 版：`graph.cypher`（建圖腳本）、`metrics.json`（同內容的結構化版本）、`bridge.cypher` |
| `kg/source/` | 知識圖譜原始文件（`graph.docx`、`standard_suggestion.xlsx`） |
| `kg/dumps/` | Neo4j 資料庫備份（2026-05-10） |
| `data/labels/` | 舊雛形用的動作描述標註資料 |
| `data/samples/` | 舊雛形的測試輸入（6 個 JSON，共 64 筆） |
| `schemas/` | 雷達事件格式草案（**未經確認**） |
| `legacy/prototype/` | 舊雛形程式，僅供參考與比對，不再開發 |

## 從哪裡開始讀

1. `docs/01_現況架構.md`：舊雛形怎麼運作、有哪些問題。
2. `docs/02_目標架構.md`：新系統的方向。
3. `docs/03_待確認事項.md`：開始寫程式前要先確認的事。

## 設定

密碼與金鑰一律用環境變數，不寫進程式碼。複製 `.env.example` 為 `.env` 後填入；`.env` 已列在 `.gitignore`。
