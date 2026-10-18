# Radar_Agent

名詞定義見 `CONTEXT.md`，架構決策見 `docs/adr/`，審查規則見 `REVIEW.md`。

## Agent skills

### Issue tracker

Issue 記在 GitHub Issues（`Deepfens-AI/Radar_Agent`）。見 `docs/agents/issue-tracker.md`。

### Triage labels

沿用五個預設標籤：`needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`。見 `docs/agents/triage-labels.md`。

### Domain docs

single-context：根目錄一份 `CONTEXT.md` 加 `docs/adr/`。見 `docs/agents/domain.md`。

## 合併 PR 之前

用 `/mattpocock-skills:code-review` 審查這個分支：固定點是 `origin/main`，需求依據是對應的 issue；沒有 issue 時，用 PR 說明。「規範」「需求」兩軸找到的每一項都處理完才合併：修正後推上去，或在 PR 說明寫明不修的理由。
