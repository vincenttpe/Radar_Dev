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
git fetch --quiet origin            # 失敗就註明「落後數可能不是最新」，繼續
git status --porcelain=v1 --branch
git rev-parse HEAD
git rev-list --count origin/main..HEAD   # 分支上 main 以外的 commit 數
git rev-list --count HEAD..origin/main   # main 上這個分支還沒有的 commit 數（只在沒有追蹤的遠端分支時用）
git log origin/main --merges --format='%H %P %s' | grep -m1 "from [^ ]*/$(git branch --show-current)\$"
git log -5 --format='%h %s（%cr）' origin/main
```

不是 git repo，或 origin 網址不含 `radar_agent` 也不含 `radar_dev`（不分大小寫；`Deepfens-AI/Radar_Agent` 與 `vincenttpe/Radar_Dev` 都算），就回報「這個資料夾不是 Radar_Agent」並結束。

## 回報格式

繁體中文。先一行結論（已同步／需要處理），再列：

| 項目 | 值 |
|---|---|
| 分支 | 目前分支（追蹤的遠端分支，沒有就寫「還沒推上 GitHub」） |
| 落後 | `[behind N]` 的 N；分支沒有追蹤的遠端分支時，改填 `HEAD..origin/main` 的數目並註明「相對 origin/main」 |
| 未 push | `[ahead N]` 的 N；分支沒有追蹤的遠端分支時寫「—（見分支列）」 |
| 未 commit | `status` 第二行起的檔案數 |

接著只列適用的下一步：

| 狀況 | 下一步 |
|---|---|
| 落後 | 先 `git pull`（分支沒有追蹤的遠端分支時改為 `git merge origin/main`；有未 commit 的檔案就先 commit 再 merge） |
| 在 `main` 上有未 commit 檔案或未 push 的 commit | 先開分支（`git checkout -b <名稱>`）再 commit，透過 PR 合併；不要直接推 main |
| 分支還沒推上 GitHub，且 `rev-list` 不為 0 | `git push -u origin <分支>`，到 GitHub 開 PR（範本見 `.github/pull_request_template.md`） |
| 有未 commit 的檔案 | `git add`、`git commit` |
| 有未 push 的 commit（分支有追蹤的遠端分支） | `git push`，到 GitHub 開 PR（範本見 `.github/pull_request_template.md`） |
| 分支已合併進 main：上面最後一個指令找到合併這個分支的 merge commit，它的第二個 parent（第 3 欄）等於 `git rev-parse HEAD`，而且沒有未 commit 的檔案 | 回 main 再 `git pull`；要繼續改就從最新的 main 開新分支 |
| 不在 `main`、`rev-list` 為 0，但不符合上一列（例如剛從 main 開出來） | 分支上還沒有新的 commit，照常開始改 |

「已合併」那一列只認得 merge commit 合併（目前 main 的慣例）。PR 若改用 squash 或 rebase 合併，這一列不會成立，表格會繼續提示 push 或開 PR；使用者說 PR 已經合併時就是這個原因，請使用者到 GitHub 確認。

最後列 `origin/main` 最近 5 個 commit。

## 必須遵守

- 不執行 `pull`、`commit`、`push`、`checkout`、`reset`、`stash`，也不改任何檔案；使用者要你代做時，先確認再做。
- 雲端工作階段（分支名像 `claude/...`）也照實回報：雲端的改動同樣要 push 上去才會留下。分支已合併時，雲端工作階段要沿用指定的分支名，下一步改成「從最新的 main 重開同名分支」。
