# 知識圖譜

## v1（來自舊雛形）

5 個指標，各自分級並附建議與建議數值：

| 指標 | 量測方式（取自等級名稱） | 等級數 |
|---|---|---|
| 下肢肌力 | 起站 30 秒次數 | 5 |
| 單腳站立 | 單腳站立秒數 | 5 |
| 來回行走 | 2.44 公尺來回行走 | 5 |
| 心肺耐力 | 原地抬腳 2 分鐘 | 5 |
| 步行速度 | 步行速度 m/s | 4（沒有 4 分等級） |

- `v1/graph.cypher`：建圖腳本，從 `source/graph.docx` 抽出，僅修正下方列出的用詞。**這是 v1 的正本。**
- `v1/metrics.json`：同一份內容的結構化版本，由 `graph.cypher` 解析產生，方便程式讀取。改內容時改 `graph.cypher`，再重新產生 JSON。
- `v1/bridge.cypher`：把舊雛形的 Class 節點接到 Metric 的查詢。
- `v1/patch_wording.cypher`：對已載入舊備份的資料庫套用下方的用詞修正，只改 4 個節點。
- `dumps/`：2026-05-10 的 Neo4j 備份。依原 README 的查詢推測含舊雛形的 Sentence、Class、Score 節點，內容未逐一核對。

## 與原始文件的差異

`graph.cypher` 與 `metrics.json` 直接在 v1 修正了以下用詞，未走下方的審核流程，也未升版。`source/graph.docx` 與 `dumps/` 的備份仍是舊用詞，要拿到修正後的資料庫有兩條路：

- **已載入 `dumps/` 備份的資料庫**：執行 `v1/patch_wording.cypher`，只改 4 個 Suggestion 節點，其他（含舊雛形的 Sentence 向量）都保留。
- **全新建庫**：執行 `v1/graph.cypher`。注意它後半段用 `LOAD CSV FROM 'file:///sorted_data.csv'` 建舊雛形的 Sentence／Class／Score 節點，執行前要把 `data/labels/sorted_data.csv` 複製到 Neo4j 的 import 資料夾，建完再跑 `legacy/prototype/build_embeddings.py` 補上向量，否則舊雛形查不到句子。

- **下肢肌力**、**心肺耐力**：原本 4 分（很好）的建議寫「…良好」、3 分（尚好）的寫「…很好」，用詞與等級顛倒；已互換為 4 分「…很好」、3 分「…良好」。

## 更新規則（提案）

更新來源有三：學術資料重新編碼、實際數據與現場回饋、現有 v1。因為內容與醫療行為相關，**不做全自動更新**，程式只能整理候選與比對差異，寫入一律由人審核後執行（見 `docs/02_目標架構.md`「知識圖譜的更新來源」）。

知識圖譜只存知識，不存個人資料。內容修改需審核後才能進版（目前由公司審核，之後是否改由 PT 或醫師審核待定，見 `docs/03_待確認事項.md` 第 6 項），每次修改升一個版本（v2、v3…），舊版本保留。
