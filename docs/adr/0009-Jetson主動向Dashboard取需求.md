---
status: proposed（2026-11-02 提議，待和 Dashboard 負責人確認後改為 accepted）
---

# Jetson 主動向 Dashboard 取需求、傳回文字；Dashboard 不連進 Jetson

使用者登入雲端的 Dashboard，帳號決定看得到哪個機構、哪些裝置。使用者要報告或打字提問時，Dashboard 把需求放進一個佇列；
機構現場的 Jetson **主動往外**用 HTTPS 輪詢這個佇列，取走自己機構的需求，再向 Dashboard 分頁取那段時間的裝置事件，
用在地模型與範本產生文字（ADR 0007），通過輸出檢查後把 JSON 傳回。Dashboard 只排版顯示，不改字、不自己算數字（`docs/16`）。

需求只寫「哪台裝置、哪段時間、哪種需求」，裝置事件由 Jetson 另外分頁取：本 repo 用的裝置事件送到雲端，送到 Jetson 的是
雷達點雲轉成的描述句（`docs/02`，A1 回覆；A23 待確認），較長時間的資料也只有 Dashboard 有。

## 考慮過的做法

- **Dashboard 連進 Jetson 呼叫 API**：Jetson 在機構內網、NAT 或防火牆後面，要開對外埠或打通道，資安曝險大（B12），
  機構網路也不一定允許。
- **使用者直接開 Jetson 的網頁**：Jetson 要另做帳號與權限，也拿不到裝置事件與長期資料。只適合資料不同步到雲端的客戶（`docs/02`），
  等真的有這類客戶再做。
- **WebSocket 或 MQTT 長連線**：需求一進來就送到 Jetson，延遲較短，但連線管理與斷線重連較複雜。第一階段延遲以模型秒數為主，
  輪詢間隔（暫定 5 秒）不是瓶頸；之後需要更即時再換。

## 後果

- Jetson 不開任何對外埠；Dashboard 的網址必須是 HTTPS，憑證要驗證；Jetson 的身分憑證（token）不進 repo、不寫進 log（B12，維運決定存放方式）。
- 需求裡的提問原文會經過佇列送到 Jetson（Dashboard 本來就有這段文字）；本 repo 不保存、不寫進 log（`docs/16`「提問」）。
  傳回的文字含地點與事件經過，屬「個人資料的流向」，改連線程式要走單向門審查。
- Jetson 停擺時需求留在佇列，Dashboard 要逾時顯示「暫時無法產生」；跌倒即時警示不經過這條路，不受影響（ADR 0006）。
- 約定細節見 `docs/23_Dashboard連線API草案.md`；Dashboard 負責人確認前，本 repo 只用本機的假 Dashboard 與模擬資料測試，不連真實雲端。
