# Demo 原型頁（**模擬資料**）

照護人員畫面的原型：一間浴廁一個上午的事件依時間重播，左邊是處置提示（通知，新的在上），右邊是照護紀錄草稿逐行累積，接著是事件摘要與提問，最後是兩週使用趨勢。規劃見 `docs/11_Demo規劃.md`「Demo 原型頁」。線上預覽：https://claude.ai/artifact/2UMoTEQwohsQNFpGPWocbe （需由擁有者開分享權限；不會隨 repo 自動更新）。

| 檔案 | 內容 |
|---|---|
| `prototype.html` | 產生出來的頁面，直接用瀏覽器開；不連網也能顯示（字型會改用系統字型） |
| `template.html` | 頁面的版面、樣式與互動；改畫面請改這裡 |
| `build.py` | 跑 `radar-caregiver` 的同一套函式，把輸出放進 `template.html`，產生 `prototype.html` |
| `dashboard.html`、`dashboard_template.html`、`dashboard_build.py` | 四種角色（居服員、家屬、系統管理人員、維運後台）的 Dashboard 試做頁（模擬資料），評估要不要在官網之外提供更順手的介面；`python demo/dashboard_build.py` 產生，網址加 `#caregiver`、`#family`、`#admin`、`#ops` 切換（後面加 `/calm` 是「平靜的上午」情境），評估方式見 `docs/17_介面易用性評估.md`。系統管理人員可以一鍵回報裝置問題（回報單自動帶入遠端判斷需要的資訊）；裝置地點查 `device_registry.json`（模擬的裝置設定紀錄）；維修紀錄與維運後台（`#ops`）用 `maintenance_sim.json`（模擬） |
| `usability_kit.html`、`usability_kit.py` | 真人試測用紙（可列印，A4 共 6 頁）：主持人記錄表與受測者的 SUS 問卷；任務與題目從 `docs/17_介面易用性評估.md` 讀，`python demo/usability_kit.py` 產生 |
| `flow_check.html`、`flow_build.py` | 流程確認頁（模擬資料，內部用）：測試看板暫代官網 Dashboard，依序列出 ① 請求（API 草案）→ ② 範圍內的資料 → ③ `radar-caregiver --json --from --to` 的原始輸出 → ④ 照原文顯示，並列出待決事項；`python demo/flow_build.py` 產生，說明見 `docs/16_Dashboard對接說明.md`「流程確認頁」 |
| `kg_demo.html`、`kg_template.html`、`kg_build.py` | 知識圖譜 Demo（模擬資料、換算規則草案，內部確認功能用）；`python demo/kg_build.py` 產生，見 `docs/11_Demo規劃.md`「知識圖譜 Demo」 |

```bash
python demo/build.py    # 改了 template.html、caregiver.py 的用語或 data/samples_demo/ 之後都要重跑
```

- 頁面上的句子**全部**來自 `caregiver_report()` 與 `trend_text()`，並先通過 `check_output()`、`check_trend()`；頁面不自己寫事件句子。只有卡片上的標籤（跌倒警報、主動呼救、久待警報、回覆沒事、起身解除、裝置斷線）寫在 `build.py` 的 `KINDS`，處置提示改了而 `KINDS` 沒跟著改，建置會失敗。
- **提問**：六題的答案來自 `radar-ask --pick` 的同一套函式（`event_ask.pick`，不經模型），建置時先通過輸出檢查。這一頁沒有接上在地模型：打字送出一律顯示「沒有對應到固定問題」並請人從選單點題，送出後輸入框清空、不保留原文。重播中和事件摘要一樣先不給答案。
- 頁面頂端一直標明「模擬資料」與「句子由範本產生，尚未接上在地模型」。
- 操作：「下一個事件」逐一出現（鍵盤 → 也可以），「從頭重播」回到 08:00，「全部顯示」一次看完。網址加 `#replay`（`prototype.html#replay`）打開就從 08:00 開始；網址加 `#gaps` 會顯示「Gap 一覽」與畫面上的編號，**只在內部演示用**（`docs/13_內部演示流程.md`「用畫面說明 Gap」）；重播時會收起標題下的說明文字，讓 1366×768 的投影畫面看得到全部紀錄（`docs/12_Demo預演.md`）。警報解除時，前面那則要到場的通知會標上「HH:MM 已解除」。
- **實測資料版**：`python demo/build.py --events outputs/field/FIELD-01.jsonl --place "1 號廁所"`，輸入要先用 `radar-field-data mask` 遮罩（還有像 MAC 的字串會拒絕產生），輸出固定寫到 `outputs/field_prototype.html`（不會被 commit），橫幅改成「實測資料」、沒有兩週趨勢，不提供發布用的版本。步驟見 `docs/11_Demo規劃.md`「10/19 實測資料」。
- `tests/test_prototype.py` 檢查：`prototype.html` 與 `build.py` 的輸出一致、頁面句子與 `radar-caregiver` 輸出逐行相同、提問的答案與 `radar-ask --pick` 相同、在線不足的日子沒有畫成 0 次、模擬資料的標示還在。
