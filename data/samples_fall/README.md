# 動作描述 JSON（含 fall 欄位）

2026-10-08 取得的樣本，1 天 11 筆。格式與舊雛形的 `data/samples/` 相同（`Timestamp`、`Motion`、`Speed`），多一個 `fall` 欄位（`"yes"`／`"no"`）。

- `Timestamp`：本地時間，12 小時制含 AM/PM，**沒有時區**。
- `Motion`：英文動作描述句，可用 `->` 串接多段。
- `Speed`：`Slow`／`Normal`／`Fast`。
- `fall`：是否跌倒。樣本最後一筆 `"A person slumped from the chair onto the floor."` 為 `"yes"`。

沒有事件編號、裝置編號、長者代號、信心值、持續時間。這份格式與 `data/samples_device_event/` 的裝置事件之間的關係待確認（見 `docs/03_待確認事項.md` 第 1 項）。
