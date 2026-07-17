#!/usr/bin/env bash
# FusionBid 登录态初始化 (Linux/macOS)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> FusionBid 登录态初始化"
echo "将打开可见浏览器，请手动登录；不绕过验证码；状态写入 data/browser_states/"

if [[ ! -d backend/.venv ]]; then
  (cd backend && python3 -m venv .venv && .venv/bin/pip install -q -U pip && .venv/bin/pip install -q -e ".[dev,full]")
fi

# shellcheck disable=SC1091
source backend/.venv/bin/activate

if ! python -c "import playwright" 2>/dev/null; then
  pip install -q playwright
  python -m playwright install chromium
fi

cd backend
python -m app.tools.login_init "$@"
