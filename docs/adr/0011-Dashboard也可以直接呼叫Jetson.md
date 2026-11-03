---
status: accepted（2026-11-03，repo 擁有者在對話中決定；網址、欄位、憑證做法待和 Dashboard 負責人約定）
---

# Dashboard 也可以直接呼叫 Jetson 的 API；和 Jetson 主動輪詢（ADR 0009）兩種並存

智慧雨林計畫預計 11 月下旬至 12 月上旬期中審查，範疇是「Jetson Orin 上，把 Dashboard 的裝置時間軸資料結合在地語言模型」。
Demo 用的 Jetson Orin 在公司內已設定固定的 public IP（位址不寫進 repo，由維運保管），Dashboard 可以直接連到它。
所以除了 ADR 0009 的輪詢之外，再加一種**由 Dashboard 呼叫**的方式：

- Dashboard 把一筆需求（報告、打字提問、點題）**連同那段時間的裝置事件**用 HTTPS POST 給 Jetson（`radar_agent.jetson_api`）。
- Jetson 用和輪詢方式同一個 `handle()` 產生文字、通過輸出檢查，**在同一個回應裡**傳回 `docs/23` ③ 的 JSON。Jetson 不回頭連 Dashboard。
- ADR 0009 的輪詢（`radar_agent.dashboard_link`）保留不動：之後正式上線、或機構網路不允許對外開埠時，再決定用哪一種。

請求與回應見 `docs/23`「另一種方式：Dashboard 直接呼叫 Jetson」。

## 考慮過的做法

- **只留輪詢（ADR 0009）**：不開對外埠，資安最單純；但 Dashboard 要做需求佇列與三支 API，期中審查前時間緊。
- **只做 Dashboard 呼叫，拿掉輪詢**：機構現場的 Jetson 多半在 NAT 或防火牆後面，對外開埠不一定可行（B11、B12），太早拿掉會失去退路。
- **Dashboard 只給裝置與時間、Jetson 再回頭向 Dashboard 取事件**：Dashboard 要多做一支 API，Jetson 也要能連出去；放在請求裡一次往返就完成。

## 後果

- Jetson 會有一個對外開放的埠，屬單向門「個人資料的流向」：請求裡有提問原文與長者的動作紀錄。程式強制：
  - 聽 127.0.0.1 以外的位址時，一定要 HTTPS（`--cert`、`--key`）與來源 IP 清單（`--allow`，Dashboard 的出口 IP）；不在清單裡的來源在 TLS 之前就斷線。
  - 每個請求要帶 token（環境變數 `RADAR_JETSON_API_TOKEN`，至少 32 個字元）；token 不進 repo、不寫進 log。
  - 請求裡的紀錄一定要是這台裝置的（topic 第二段等於 `device`），不符就回 `failed`：輪詢時 Jetson 自己照裝置取資料，這裡改由 Dashboard 放進來，要防送錯。
  - 請求大小、紀錄筆數有上限；同時最多 4 條連線；一次只處理一筆，忙的時候回 503。
  - 提問原文不保存、不寫進 log；log 只記來源 IP、需求編號、種類、結果與秒數。
- 程式管不到的，要在 Jetson 上設定（`docs/23` 有清單）：防火牆只放行 Dashboard 的出口 IP 到這個埠；關掉 rpcbind（埠 111）與 CUPS（埠 631）（B12 實測線索）；
  Ollama 只聽 127.0.0.1；Demo 頁面（`demo/jetson_serve.py`）照舊只聽 127.0.0.1。
- 用 IP 連線沒有網域名稱，憑證先用自簽、由 Dashboard 釘選（只信任這一張）；正式上線改用正式憑證或通道，由維運決定（B12）。
- 細節與待確認的題目見 `docs/23` 與會議清單 B13。Jetson 停擺或忙碌時，Dashboard 會直接收到連線失敗或 503，要自己顯示「暫時無法產生」並決定要不要重送；跌倒即時警示不經過這條路（ADR 0006）。
- 兩種方式產生的 JSON 完全相同（測試比對），`docs/16` 的顯示規則不用改。
