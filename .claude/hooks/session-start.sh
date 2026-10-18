#!/bin/bash
# 雲端工作階段啟動時安裝開發依賴，讓 ruff、mypy、pytest 與 radar-* 指令可以在本機跑（同 CI 的 tests.yml）。
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"
# 可編輯安裝：程式會讀 repo 裡的 schemas/、kg/（見 pyproject.toml）
python -m pip install --quiet --disable-pip-version-check -r requirements-dev.txt

# 環境預裝的 uv 工具（~/.local/bin 的 pytest、mypy、ruff）是獨立環境，沒有本專案與其依賴，
# 版本也不一定符合 pyproject.toml，讓剛裝好的指令排在前面。
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  bindir="$(python -c 'import sysconfig; print(sysconfig.get_path("scripts"))')"
  echo "export PATH=\"$bindir:\$PATH\"" >> "$CLAUDE_ENV_FILE"
fi
