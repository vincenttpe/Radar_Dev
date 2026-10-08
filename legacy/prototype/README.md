# 舊雛形（僅供參考）

Health-care-report-LLM 的程式，2026-10-06 zip 版本。**不再開發**，保留目的是查舊邏輯與比對新系統的輸出。說明見 `docs/01_現況架構.md`，搬移時的修改見 `docs/04_遷移紀錄.md`。原始 README 在 `README.original.md`。

## 執行（未在新環境驗證）

需要 CUDA（或接受 CPU）、Docker 中已載入 `kg/dumps/` 的 Neo4j，且外部搜尋預設會連網。

```bash
cd legacy/prototype
pip install -r requirements.txt   # torch 另外安裝
export NEO4J_PASSWORD=...         # 不寫進程式
python3 Project1.py               # 預設讀 data/samples/move.json
```
