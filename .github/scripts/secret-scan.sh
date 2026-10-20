#!/usr/bin/env bash
# 密碼掃描（.github/workflows/secrets.yml 呼叫；tests/test_packaging.py 測它的行為）。
# 用法：secret-scan.sh <gitleaks 執行檔> [起點 commit 終點 commit]
#   有給起訖：只掃這段 commit（PR、push 到 main）；沒給：掃全部分支的完整歷史。
# 範圍拿不到就失敗：gitleaks 遇到無效範圍只記一行錯誤、照樣顯示沒發現，不能讓檢查悄悄變綠。
set -euo pipefail
gitleaks="$1"
from="${2:-}"
to="${3:-}"

# gitleaks 一定會讀被掃目錄的 .gitleaksignore；允許清單只能放 .github/（單向門），根目錄有就擋下
if [ -e .gitleaksignore ]; then
  echo "::error::repo 根目錄不能有 .gitleaksignore；要忽略的項目由人決定後放 .github/gitleaksignore"
  exit 1
fi

if [ -n "$to" ]; then
  for c in "$from" "$to"; do
    if ! git cat-file -e "${c}^{commit}" 2>/dev/null; then
      echo "::error::掃描範圍的 commit 拿不到：${c:-（空白）}。可能是 force push 或分支重建；請從 Actions 手動執行一次全部歷史的掃描"
      exit 1
    fi
  done
  range="$from..$to"
else
  range="--all"
fi
echo "掃描範圍：$range"

# --config、--gitleaks-ignore-path 固定讀 .github/ 底下的檔；--ignore-gitleaks-allow：程式碼裡的 gitleaks:allow 註解不算數
# --redact：結果裡的密碼一律遮掉；-v：列出檔案、行號、commit，人才知道要去哪裡處理
"$gitleaks" git . \
  --config .github/gitleaks.toml \
  --gitleaks-ignore-path .github/gitleaksignore \
  --ignore-gitleaks-allow \
  --redact -v --no-color --no-banner \
  --exit-code 1 \
  --log-opts="$range"
