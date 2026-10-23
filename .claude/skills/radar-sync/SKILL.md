---
name: radar-sync
description: 檢查本機（或這個工作階段）Radar_Agent checkout 的 git 同步狀態，告訴使用者下一步該 pull、開分支、commit、push 還是開 PR。使用者輸入 /radar-sync、問「要不要 pull／push」「同步了沒」、開工前或收工前時使用。只讀不寫。
---

# Radar_Agent 同步檢查

開工先 pull、改動走分支與 PR、收工一定 push：這個 skill 只負責**看狀態、提醒下一步**，不代替使用者做。

## 步驟

依序執行，**都只讀不寫**（`git fetch` 只更新遠端追蹤分支，不動工作樹）：

```bash
git rev-parse --show-toplevel
git remote get-url origin
git fetch --quiet --prune origin    # 失敗就註明「落後數可能不是最新」，繼續；--prune 讓已刪除的遠端分支顯示成 [gone]
git status --porcelain=v1 --branch
git rev-parse HEAD
git rev-list --count origin/main..HEAD   # 分支上 main 以外的 commit 數
git rev-list --count HEAD..origin/main   # main 上這個分支還沒有的 commit 數（只在沒有可用的追蹤分支時用）
git log origin/main --merges --format='%H %P %s' | grep -m1 "from [^ ]*/$(git branch --show-current)\$"
git log -5 --format='%h %s（%cr）' origin/main
```

不是 git repo，或 origin 網址不含 `radar_agent` 也不含 `radar_dev`（不分大小寫；`Deepfens-AI/Radar_Agent` 與 `vincenttpe/Radar_Dev` 都算），就回報「這個資料夾不是 Radar_Agent」並結束。

## 回報格式

先判斷分支有沒有**可用的追蹤分支**：`status` 第一行（`## <分支>...<追蹤分支>`）寫的追蹤分支正是 `origin/<目前分支>`，而且沒有 `[gone]`。沒有追蹤分支、追蹤的是別的分支（例如從 `origin/main` 開出來的），或遠端分支已刪除（`[gone]`，PR 合併後 GitHub 常會自動刪），都算沒有。

繁體中文。先一行結論（已同步／需要處理），再列：

| 項目 | 值 |
|---|---|
| 分支 | 目前分支（可用的追蹤分支；沒有就寫「沒有可用的追蹤分支」並附原因：未設定、追蹤 `<名稱>` 或遠端已刪除） |
| 落後 | 有可用的追蹤分支時填 `[behind N]` 的 N（相對自己的遠端分支）；沒有時填 `HEAD..origin/main` 的數目並註明「相對 origin/main」 |
| 未 push | 有可用的追蹤分支時填 `[ahead N]` 的 N；沒有時寫「—」 |
| 未 commit | `status` 第二行起的檔案數 |

表格裡的**新 commit 數** ＝ `origin/main..HEAD` 的數目。

接著由上往下比對，只列適用的下一步，照列序做。**「已合併」「狀態不明」「在 main 上」三列成立時只列那一列**（未 commit 的檔案與推送都在列內處理）。推送一律是最後一步，放在「推送」那列，因為 commit 與合併 main 都會產生要推上去的 commit：

| 狀況 | 下一步 |
|---|---|
| **已合併**：`grep -m1` 那行找到合併這個分支的 merge commit，它的第二個 parent（第 3 欄）等於 `git rev-parse HEAD` | 回 main 再 `git pull`；要繼續改就從最新的 main 開新分支。有未 commit 的檔案時不要 commit 在這個已合併的分支上：先 `git stash -u`，回 main `git pull`、開新分支，`git stash pop` 後 commit，再 `git push -u origin <新分支>`、到 GitHub 開 PR |
| **狀態不明**：不在 `main`、遠端分支已刪除（`[gone]`）、新 commit 數大於 0，而且 `grep -m1` 那行沒有輸出 | git 判斷不出 PR 是否已合併（可能是用 squash 或 rebase 合併）。請使用者先到 GitHub 確認：已合併就照「已合併」處理；沒有合併就照下面幾列 commit、合併 main、推送 |
| **在 main 上**：有未 commit 的檔案或新 commit 數大於 0 | 先開分支（`git checkout -b <名稱>`，未 commit 的檔案會跟著過去）再 commit；也落後時接著 `git merge origin/main`；最後 `git push -u origin <名稱>`，到 GitHub 開 PR。不要直接推 main |
| **未 commit**：有未 commit 的檔案 | `git add`、`git commit` |
| **落後**（數目見回報表） | `git pull`；沒有可用的追蹤分支時改為 `git merge origin/main` |
| **推送**：不在 `main`，而且「未 commit」有列出、`[ahead N]` 大於 0，或沒有可用的追蹤分支且新 commit 數大於 0 | 做完上面幾列之後推送：有可用的追蹤分支用 `git push`，沒有就用 `git push -u origin <分支>`；還沒開 PR 就到 GitHub 開（範本見 `.github/pull_request_template.md`）。`grep -m1` 那行有輸出（這個分支名以前合併過）時加註：這些是合併之後的新 commit，要開新的 PR |
| **還沒開始**：不在 `main`、新 commit 數為 0、沒有未 commit 的檔案 | 分支上還沒有新的 commit，照常開始改 |

「已合併」只認得 merge commit 合併（目前 main 的慣例）。PR 用 squash 或 rebase 合併時判斷不出來：遠端分支已刪除而且看得到 `[gone]` 時會落到「狀態不明」；遠端分支還在，或當初沒加 `-u` 推送（看不到 `[gone]`）時，分支看起來跟還在審查中的分支一樣，表格照常列出。使用者說 PR 已經合併時就是這個原因，改照「已合併」處理。

最後列 `origin/main` 最近 5 個 commit。

## 必須遵守

- 不執行 `pull`、`commit`、`push`、`checkout`、`reset`、`stash`，也不改任何檔案；使用者要你代做時，先確認再做。
- 雲端工作階段（分支名像 `claude/...`）也照實回報：雲端的改動同樣要 push 上去才會留下。分支已合併時，雲端工作階段要沿用指定的分支名，下一步改成「從最新的 main 重開同名分支」。
