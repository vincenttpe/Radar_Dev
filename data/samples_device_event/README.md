# 裝置雷達動作事件

兩份資料：

| 檔案 | 內容 | 取得 |
|---|---|---|
| `absence.json` | 雲端從裝置收到的一筆原樣 | 2026-10-08 |
| `mqtt_messages_2026-10-12.json` | 裝置 MQTT 訊息規格：13 種訊息的範例 payload | 2026-10-12 |

規格是範例，不是實際收到的資料；`absence.json` 的內容與規格中的「無人」相同（鍵的順序不同）。

## MQTT 訊息規格（2026-10-12）

所有訊息都送到同一個 topic：`radar10/{{identifier}}`。`{{identifier}}` 是裝置識別碼，payload 裡沒有裝置編號。

| 分類 | 訊息 | `event` | `trigger` | `mode` | `userReply` | `delaysec` |
|---|---|---|---|---|---|---|
| 連線 | 在線 | `online` | | | | |
| 連線 | 離線 | `offline` | | | | |
| 在場 | 有人 | `presence` | `track` | `radar` | false | |
| 在場 | 無人 | `absence` | `track` | `radar` | false | |
| 活動 | 活躍 | `moving` | `track` | `radar` | false | |
| 活動 | 一般靜止 | `static` | `track` | `radar` | false | |
| 活動 | 靜止滯留警報 | `longstay` | `timeout` | `radar` | false | 600 |
| 跌倒警報 | 雷達偵測、無回應 | `fall` | `timeout` | `radar` | false | 10 |
| 跌倒警報 | 雷達偵測、語音回覆求救 | `fall` | `conversation` | `fusion` | true | |
| 跌倒警報 | 主動呼救、語音確認 | `fall` | `conversation` | `speech` | true | |
| 跌倒警報 | 主動呼救、無回應 | `fall` | `timeout` | `speech` | false | 10 |
| 解除 | 雷達偵測起身 | `resolved` | `standUp` | `radar` | false | |
| 解除 | 語音回覆正常 | `resolved` | `noNeedHelp` | `speech` | true | |

`fall` 與 `resolved`、`longstay` 另有 `confidence`，範例都是 100。

各欄位依規格的說明：

- `event`：事件種類。沒有之前聽說的 `vacant`，「無人」是 `absence`。
- `trigger`：觸發方式。`track` 出現在雷達追蹤的狀態訊息（有人、無人、活躍、一般靜止）；`timeout` 是等候超過 `delaysec` 秒；`conversation` 是語音對話得到回覆；`standUp`、`noNeedHelp` 是解除警報的原因。
- `mode`：來源。`radar` 雷達、`speech` 語音、`fusion` 雷達偵測加語音回覆。
- `userReply`：有沒有收到長者的語音回覆。跌倒警報中 false 表示等候逾時、沒有回覆。
- `delaysec`：等候秒數，只出現在 `trigger` 為 `timeout` 的訊息。
- **DistressCall**：`event` 為 `fall` 且 `mode` 為 `speech` 的訊息，代表語音偵測到主動呼救，規格註明會映射為 DistressCall 狀態，不一定真的跌倒。
- `static`：雷達原本偵測到有人，tracker 開始失去此人，但 decision 判定此人還在空間。
- `moving`：雷達開始偵測到有人，或靜止狀態時 tracker 判定有 ID 接管。

## 仍缺的資訊

- payload 沒有時間戳與序號；時間戳是否由雲端收到時補上，待確認（會議清單 A4、A11）。
- 裝置識別碼只在 topic；與裝置 MAC、場域、長者代號怎麼對應，待確認（A5）。
- `confidence` 的範圍與意義、`delaysec` 能否依場域設定，待確認（A3）。

與 `data/samples_fall/` 的動作描述 JSON 是兩種不同格式，彼此關係待確認（見 `docs/03_待確認事項.md` 第 1 項）。
