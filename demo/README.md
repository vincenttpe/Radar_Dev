# Demo 原型頁（**模擬資料**）

照護人員畫面的原型：一間浴廁一個上午的事件依時間重播，左邊是處置提示（通知，新的在上），右邊是照護紀錄草稿逐行累積，最後是兩週使用趨勢。規劃見 `docs/11_Demo規劃.md`「Demo 原型頁」。

| 檔案 | 內容 |
|---|---|
| `prototype.html` | 產生出來的頁面，直接用瀏覽器開；不連網也能顯示（字型會改用系統字型） |
| `template.html` | 頁面的版面、樣式與互動；改畫面請改這裡 |
| `build.py` | 跑 `radar-caregiver` 的同一套函式，把輸出放進 `template.html`，產生 `prototype.html` |

```bash
python demo/build.py    # 改了 template.html、caregiver.py 的用語或 data/samples_demo/ 之後都要重跑
```

- 頁面上的句子**全部**來自 `caregiver_report()` 與 `trend_text()`，並先通過 `check_output()`、`check_trend()`；頁面不自己寫事件句子。只有卡片上的標籤（跌倒警報、主動呼救、久待警報、回覆沒事、起身解除、裝置斷線）寫在 `build.py` 的 `KINDS`，處置提示改了而 `KINDS` 沒跟著改，建置會失敗。
- 頁面頂端一直標明「模擬資料」與「句子由範本產生，尚未接上在地模型」。
- 操作：「下一個事件」逐一出現（鍵盤 → 也可以），「從頭重播」回到 08:00，「全部顯示」一次看完。警報解除時，前面那則要到場的通知會標上「HH:MM 已解除」。
- `tests/test_prototype.py` 檢查：`prototype.html` 與 `build.py` 的輸出一致、頁面句子與 `radar-caregiver` 輸出逐行相同、在線不足的日子沒有畫成 0 次、模擬資料的標示還在。
