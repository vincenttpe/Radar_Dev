# Triage 標籤

技能用五個固定的 triage 角色溝通。這份表把角色對應到本 repo issue 追蹤實際使用的標籤字串。

| 技能裡的角色 | 本 repo 的標籤 | 意思 |
| --- | --- | --- |
| `needs-triage` | `needs-triage` | 維護者要先評估 |
| `needs-info` | `needs-info` | 等回報者補資訊 |
| `ready-for-agent` | `ready-for-agent` | 規格完整，代理可以直接做 |
| `ready-for-human` | `ready-for-human` | 要由人來做（需要判斷、外部權限或現場測試） |
| `wontfix` | `wontfix` | 不處理 |

標籤由 `.github/workflows/labels.yml` 建立與維護（2026-10-19）：改名稱、說明或顏色時改那個檔案，合併到 main 後會自動更新。

技能提到某個角色時（例如「貼上可交給代理的 triage 標籤」），用表中對應的標籤字串。
