# Issue 追蹤：GitHub

這個 repo 的 issue 與規格都記在 GitHub Issues（`Deepfens-AI/Radar_Agent`）。用 `gh` 指令操作；雲端工作階段沒有 `gh` 時，改用對應的 GitHub MCP 工具（`mcp__github__issue_write`、`issue_read`、`list_issues`、`add_issue_comment` 等），對同一個 repo 操作。

## 操作方式

- **開 issue**：`gh issue create --title "..." --body "..."`。多行內容用 heredoc。
- **讀 issue**：`gh issue view <number> --comments`，用 `jq` 篩留言，並一併取得標籤。
- **列 issue**：`gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'`，再加上需要的 `--label`、`--state` 篩選。
- **留言**：`gh issue comment <number> --body "..."`
- **貼上／移除標籤**：`gh issue edit <number> --add-label "..."`／`--remove-label "..."`
- **關閉**：`gh issue close <number> --comment "..."`

`gh` 在 clone 裡執行時會從 `git remote -v` 自動判斷 repo。

## PR 是否當成需求來源

**PRs as a request surface: no.**（`/triage` 讀這個旗標；改成 `yes` 代表外部 PR 也當成需求來 triage。）

設成 `yes` 時，PR 用跟 issue 一樣的標籤與狀態，改用 `gh pr` 對應的指令：

- **讀 PR**：`gh pr view <number> --comments`，diff 用 `gh pr diff <number>`。
- **列出要 triage 的外部 PR**：`gh pr list --state open --json number,title,body,labels,author,authorAssociation,comments`，只留 `authorAssociation` 為 `CONTRIBUTOR`、`FIRST_TIME_CONTRIBUTOR`、`NONE` 的（排除 `OWNER`／`MEMBER`／`COLLABORATOR`）。
- **留言／標籤／關閉**：`gh pr comment`、`gh pr edit --add-label`／`--remove-label`、`gh pr close`。

GitHub 的 issue 與 PR 共用同一組編號，單獨寫 `#42` 可能是任一種：先試 `gh pr view 42`，不是 PR 再用 `gh issue view 42`。

## 技能說「發到 issue 追蹤」時

開一個 GitHub issue。

## 技能說「取得相關的 ticket」時

執行 `gh issue view <number> --comments`。

## Wayfinding 操作

給 `/wayfinder` 用。**地圖**是一個 issue，底下的**子 issue** 是各張 ticket。

- **地圖**：一個貼了 `wayfinder:map` 標籤的 issue，內容分 Notes／Decisions-so-far／Fog。`gh issue create --label wayfinder:map`。
- **子 ticket**：以 GitHub sub-issue 連到地圖（用 `gh api` 呼叫 sub-issues endpoint）。沒開 sub-issue 功能時，把子 ticket 加進地圖內容的 task list，並在子 ticket 開頭寫 `Part of #<map>`。標籤用 `wayfinder:<type>`（`research`／`prototype`／`grilling`／`task`）。認領後指派給負責的開發者。
- **阻擋關係**：用 GitHub 原生的 issue dependencies。新增一條：`gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`，`<blocker-db-id>` 是擋住它的 issue 的數字 **database id**（`gh api repos/<owner>/<repo>/issues/<n> --jq .id`，不是 `#number` 也不是 `node_id`）。GitHub 會在 `issue_dependencies_summary.blocked_by` 回報還開著的阻擋。沒有 dependencies 功能時，改在子 ticket 開頭寫一行 `Blocked by: #<n>, #<n>`。擋住它的 issue 都關閉後，才算解除阻擋。
- **找下一張可做的**：列出地圖底下還開著的子 ticket（`gh issue list --state open`，限地圖的 sub-issues 或 task list），排除還有阻擋（`issue_dependencies_summary.blocked_by > 0`，或 `Blocked by` 那行有還開著的 issue）或已經有人認領的；依地圖順序取第一張。
- **認領**：`gh issue edit <n> --add-assignee @me`，這是工作階段的第一個寫入動作。
- **完成**：`gh issue comment <n> --body "<答案>"`，接著 `gh issue close <n>`，再在地圖的 Decisions-so-far 補一筆摘要與連結。
