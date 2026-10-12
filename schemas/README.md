# 事件格式

`event.v0.draft.json` 是**草案**，依目標架構的需要寫出，尚未經雷達與 Dashboard 負責人確認（見 `docs/03_待確認事項.md` 第 1、3、4、10 項）。欄位名稱、單位與必填項目都可能改。確認後改名為 `event.v1.json`，之後每次改版都升版本號。

**2026-10-08 與雷達端對照**：雷達負責人說明邊緣裝置的動作狀態 JSON 目前有 moving、longstay、vacant、fall 等狀態。草案的 `event_type`（fall、posture、activity、heartbeat）與 `label` 要重新對齊：`fall` 相同；moving／longstay／vacant 比較像 `label`，不是 `event_type`；vacant（無人）在草案裡沒有對應。拿到實際 JSON 樣本後再改草案。

**2026-10-08 取得兩種樣本**（見 `data/samples_fall/`、`data/samples_device_event/` 的 README）：

| 草案欄位 | 動作描述 JSON (a) | 裝置事件 (b) |
|---|---|---|
| `event_type` | 只能由 `fall` 欄位分出 fall／非 fall | `event`（absence 等）較接近 |
| `label` | `Motion` 是英文句子，不是固定標籤 | `event` |
| `confidence` | 無 | 無 |
| `measurements` | 無；只有 `Speed` 三級 | 無 |
| `voice_confirmation` | 無 | `userReply` 布林值，推測對應 |
| `timestamps.detected` | `Timestamp`，本地時間無時區 | 無，待確認由誰補 |
| `started`／`ended` | 無 | 無 |
| `event_id`／`device_id`／`subject_id` | 無 | 無 |

**2026-10-12 取得裝置 MQTT 訊息規格**（`data/samples_device_event/README.md`）：(b) 的 `event` 有 online、offline、presence、absence、moving、static、longstay、fall、resolved；fall、resolved、longstay 另有 `confidence`（範例都是 100）與（逾時類）`delaysec`。對照上表：`confidence` 在 (b) 有了來源；`voice_confirmation` 可由 `trigger`（conversation／timeout）與 `userReply` 得出；草案沒有對應的有 online／offline（連線狀態，可算涵蓋率）、resolved（解除警報）與 `mode`（radar、speech、fusion）。時間戳、`event_id`、`device_id`（只在 topic 的 `{{identifier}}`）仍沒有。草案先不改，等會議清單 A1、A3、A4、A5 確認後一次改為 v1。

兩種格式都沒有持續時間，所以草案要求 posture／activity 必填 `started`／`ended` 這一點，目前的來源給不出來；活動量統計暫時只能用事件數與 `Speed` 分布。等確認 (a)(b) 的關係與 7 天資料的實際格式後，草案改為 v1。

## 草案目前的取捨

- **不接受未定義欄位**（各層都設 `additionalProperties: false`）。事件接收要做格式檢查，未知欄位直接拒收，比默默忽略容易發現雷達端改版沒通知。雷達端若需要新增欄位，就是一次格式改版。
- **姿態與活動要有起訖時間。** `event_type` 為 `posture`、`activity` 時，`timestamps.started` 與 `timestamps.ended` 必填；持續時間由兩者相減，不另設欄位，避免兩邊對不上。跌倒與心跳事件只需要 `detected`。

這兩點也要和雷達負責人確認，見 `docs/03_待確認事項.md` 第 1、10 項。
