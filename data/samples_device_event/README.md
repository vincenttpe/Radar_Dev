# 裝置雷達簡易動作事件

2026-10-08 取得，雲端從裝置收到的一筆原樣：

```json
{"mode":"radar","event":"absence","trigger":"track","userReply":false}
```

- `mode`：來源模式，目前見到 `radar`。
- `event`：事件種類，目前見到 `absence`（無人）；雷達負責人另提過 moving、longstay、vacant、fall。
- `trigger`：觸發方式，目前見到 `track`。
- `userReply`：布林值，推測是語音確認有無回應。

這筆沒有時間戳、裝置編號、長者代號；時間戳是否由雲端收到時補上，待確認。裝置可依需要即時（realtime）把同樣的事件送到 Jetson。

與 `data/samples_fall/` 的動作描述 JSON 是兩種不同格式，彼此關係待確認（見 `docs/03_待確認事項.md` 第 1 項）。
