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

先 `git fetch origin main`，再用 `/mattpocock-skills:code-review` 審查這個分支：固定點是 `origin/main`，需求依據是 PR 說明的「需求來源」（`REVIEW.md` 第 4 節）。

找到的問題依 `REVIEW.md` 第 5 節標成阻擋或建議來處理。合併的條件：
- 單向門以外的明確阻擋：修正後重跑審查，重複到審查結果裡不再有明確的阻擋為止。
- 需要人決定的阻擋（醫療判斷、口徑、範圍）與單向門上的阻擋：只列出問題與選項，由人決定；人決定之前不合併。
