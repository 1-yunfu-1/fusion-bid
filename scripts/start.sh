#!/usr/bin/env bash
# FusionBid 本地启动脚本 (Linux/macOS)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> FusionBid 智标聚合助手 - 启动开发环境"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "已从 .env.example 创建 .env"
fi

if [[ ! -d backend/.venv ]]; then
  echo "创建 Python 虚拟环境..."
  (cd backend && python3 -m venv .venv)
fi

# shellcheck disable=SC1091
source backend/.venv/bin/activate
echo "安装后端依赖..."
pip install -q -e "backend/.[dev]"

echo "安装前端依赖..."
(cd frontend && if [[ ! -d node_modules ]]; then npm install; fi)

cleanup() {
  if [[ -n "${BACKEND_PID:-}" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    kill "$BACKEND_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

echo "启动后端 http://127.0.0.1:8000 ..."
(cd backend && uvicorn app.main:app --reload --host 127.0.0.1 --port 8000) &
BACKEND_PID=$!
sleep 2

echo "启动前端 http://127.0.0.1:5173 ..."
(cd frontend && npm run dev)
