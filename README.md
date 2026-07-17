# FusionBid 智标聚合助手

> 2026 AI 先锋未来人才大赛——超聚变企业命题  
> AI 招投标信息聚合工具：自然语言 → 多源采集 → 清洗去重 → Word 报告 → 定时增量

**默认语言**：简体中文 · **默认时区**：Asia/Shanghai  
**当前版本**：完整联调交付

仓库：https://github.com/1-yunfu-1/fusion-bid

---

## 功能概览

| 能力 | 状态 |
|------|------|
| 项目骨架 / 健康检查 / SQLite | ✅ |
| 自然语言意图解析（API→Ollama→规则） | ✅ |
| 公开源（ccgp + cebpub） | ✅ |
| 登录态源（Playwright 手动登录） | ✅ |
| 清洗 / 去重 / 增量 | ✅ |
| Word 报告 | ✅ |
| 定时任务 | ✅ |
| 一键启动（单端口托管前端+API） | ✅ |

不返回伪造招标数据。

---

## 最推荐：一键启动（给使用方）

**只需要 Python 3.12+**，不需要 Node.js。

### Windows

1. 克隆或解压本仓库  
2. 双击 `scripts\start_all.bat`，或在项目根目录双击发布包中的 `start.bat`  
3. 浏览器打开：

| 用途 | 地址 |
|------|------|
| **系统界面（请打开这个）** | **http://127.0.0.1:8000/** |
| API 文档 | http://127.0.0.1:8000/docs |
| 健康检查 | http://127.0.0.1:8000/api/health |

> **注意：**  
> - 正确界面是网页（系统概览、新建检索等），**不是**一段 JSON。  
> - **`http://127.0.0.1:5173` 仅用于前端开发热更新**，一键启动 / 正常使用请用 **8000**。  
> - API Key **不会**预置在仓库中，请在页面「设置」中填写。

首次运行会自动创建虚拟环境并安装依赖（需联网）。

制作干净发布 zip：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\package_release.ps1
```

产物在 `dist_release/`（不含 API Key、不含本机数据库）。

---

## 开发启动（可选）

适合改前端代码时使用。需要：Python ≥ 3.12、Node.js ≥ 20。

### 方式 A：后端托管已构建前端（接近生产）

```powershell
# 项目根
copy .env.example .env
cd frontend
npm install
npm run build
cd ..\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[full]"
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

访问：**http://127.0.0.1:8000/**（前端由后端托管 `frontend/dist`）

### 方式 B：前后端分离开发

终端 1 — API：

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

终端 2 — Vite 开发服：

```powershell
cd frontend
npm install
npm run dev
```

| 用途 | 地址 |
|------|------|
| 前端开发页（热更新） | http://127.0.0.1:5173 |
| 后端 API / docs | http://127.0.0.1:8000 |

Vite 已将 `/api` 代理到 8000。

或使用旧脚本：

```powershell
.\scripts\start.ps1
```

### Linux / macOS（开发）

```bash
cp .env.example .env
cd backend && python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[full]"
# 构建前端后单端口：
cd ../frontend && npm install && npm run build
cd ../backend && uvicorn app.main:app --host 127.0.0.1 --port 8000
```

---

## 测试

```bash
cd backend
pytest

cd ../frontend
npm run lint
npm run build
```

---

## 文档

- [操作文档](docs/操作文档.md)
- [LLM 双模式配置](docs/LLM双模式配置.md)
- [数据源说明](docs/数据源说明.md)
- [Demo 演示脚本](docs/Demo演示脚本.md)
- [详设文档](docs/详设文档.md)
- [发布包使用说明模板](scripts/release_README.md)
- [AGENTS.md](AGENTS.md)

### 登录态初始化

```powershell
cd backend
pip install playwright
playwright install chromium
python -m app.tools.login_init
# 或
.\scripts\login_source.ps1
```

也可在系统「数据源」页点击「启动登录浏览器」。

---

## 安全提示

- 将 `.env.example` 复制为 `.env` 后填写密钥，**切勿提交 `.env`**
- `data/llm_secrets.json`、`data/browser_states/` 禁止提交 / 外传
- 不绕过验证码与网站访问控制

---

## 目录结构

```text
.
├── backend/          # FastAPI（可托管 frontend/dist）
├── frontend/         # React + Vite
├── data/             # 运行时数据（gitignore 敏感内容）
├── docs/             # 设计与操作文档
├── scripts/          # 一键启动 / 打包 / 登录脚本
└── docker-compose.yml
```
