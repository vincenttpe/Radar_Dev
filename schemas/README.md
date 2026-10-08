# 事件格式

`event.v0.draft.json` 是**草案**，依目標架構的需要寫出，尚未經雷達與 Dashboard 負責人確認（見 `docs/03_待確認事項.md` 第 1、3、4、10 項）。欄位名稱、單位與必填項目都可能改。確認後改名為 `event.v1.json`，之後每次改版都升版本號。

## 草案目前的取捨

- **不接受未定義欄位**（各層都設 `additionalProperties: false`）。事件接收要做格式檢查，未知欄位直接拒收，比默默忽略容易發現雷達端改版沒通知。雷達端若需要新增欄位，就是一次格式改版。
- **姿態與活動要有起訖時間。** `event_type` 為 `posture`、`activity` 時，`timestamps.started` 與 `timestamps.ended` 必填；持續時間由兩者相減，不另設欄位，避免兩邊對不上。跌倒與心跳事件只需要 `detected`。

這兩點也要和雷達負責人確認，見 `docs/03_待確認事項.md` 第 1、10 項。
