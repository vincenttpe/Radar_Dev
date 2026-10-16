# Demo 用模擬資料（**模擬，不是真實紀錄**）

| 檔案 | 內容 |
|---|---|
| `bathroom_morning.jsonl` | 一間浴廁 2026-10-14 08:00–12:00 的裝置事件，一行一筆，2,708 筆 |
| `generate.py` | 產生上面那個檔案的程式；改情境請改這裡再重新產生，不要手改 jsonl |

- **全部是程式產生的模擬資料**，不是任何裝置收到的紀錄，也沒有任何長者資料。裝置名稱用 `DEMO-SIM-01`，不是真實 MAC。
- 格式照雲端存的外殼 `{timestamp, topic, resolution, payload}`（會議清單 A2）；payload 照 2026-10-12 的 MQTT 訊息規格（`data/samples_device_event/README.md`），沒有自創欄位。
- Demo 時畫面與口頭都要說明這是模擬資料（`docs/11_Demo規劃.md`「Demo 中一律標明的事」）。

```bash
python data/samples_demo/generate.py                          # 重新產生
radar-caregiver data/samples_demo/bathroom_morning.jsonl --place "1 號廁所"
```

## 情境（照計畫書的三項互補機制與「從警報到溝通」）

沒人時約每 5 秒送一筆 `absence`，有人時送 `presence` → `moving`／`static` → `absence`（會議清單 A10 的實測節奏）。

| 時間 | 情境 | 裝置事件 | Demo 要講的 |
|---|---|---|---|
| 08:05–08:09 | 一般使用 | presence → moving → absence | 平常使用不會有任何警報 |
| 08:31–08:35 | A：疑似跌倒，回覆沒事 | 08:32:10 fall（radar／timeout，等 10 秒無回應）→ 08:32:40 resolved（noNeedHelp） | 從警報到溝通：長者回覆沒事，不必白跑；不直接算誤報（C2） |
| 09:15–09:21 | B：疑似跌倒，回覆需要協助 | 09:16:20 fall（fusion／conversation）→ 09:19:30 resolved（standUp） | 雷達偵測加語音確認，確定要到場 |
| 10:02–10:07 | C：主動呼救 | 10:03:15 fall（speech／conversation）→ 10:05:50 resolved（standUp） | 第二項機制：沒有跌倒也能求救 |
| 10:30–10:52 | D：靜止滯留 | static 持續，10:45:00 longstay（timeout） | 第三項機制：異常滯留警示 |
| 11:10–11:25 | 裝置斷線 | 11:10 offline，11:25 online，中間沒有紀錄 | 斷線時寫「無法判斷」，不寫成 0 次跌倒 |

`tests/test_demo_sample.py` 會檢查：檔案與 `generate.py` 的輸出一致、`radar-caregiver` 的輸出與 Demo 預期相同、檔案裡沒有像 MAC 位址的字串。
