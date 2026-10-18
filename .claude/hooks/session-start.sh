#!/bin/bash
# 雲端工作階段啟動時安裝開發依賴，讓 ruff、mypy、pytest 與 radar-* 指令可以在本機跑（同 CI 的 tests.yml）。
# resume、compact 也會觸發；已經裝好時 pip 很快結束，PATH 也不會重複寫入。
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"
# 可編輯安裝：程式會讀 repo 裡的 schemas/、kg/（見 pyproject.toml）
# 裝不起來（例如連不上網）只提醒，不讓工作階段啟動失敗
if ! python -m pip install --quiet --disable-pip-version-check -r requirements-dev.txt; then
  echo "警告：開發依賴沒有裝好，ruff、mypy、pytest 可能無法使用；請手動執行 pip install -r requirements-dev.txt" >&2
fi

# 環境預裝的 uv 工具（~/.local/bin 的 pytest、mypy、ruff）是獨立環境，沒有本專案與其依賴，
# 版本也不一定符合 pyproject.toml，讓剛裝好的指令排在前面。
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  bindir="$(python -c 'import sysconfig; print(sysconfig.get_path("scripts"))')"
  line="export PATH=\"$bindir:\$PATH\""
  grep -qxF "$line" "$CLAUDE_ENV_FILE" 2>/dev/null || echo "$line" >> "$CLAUDE_ENV_FILE"
fi
