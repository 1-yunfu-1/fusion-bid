# FusionBid 智标聚合助手

> 2026 AI 先锋未来人才大赛——超聚变企业命题  
> AI 招投标信息聚合工具：自然语言 → 多源采集 → 清洗去重 → Word 报告 → 定时增量

**默认语言**：简体中文 · **默认时区**：Asia/Shanghai  
**当前阶段**：阶段八 · 完整联调（`1.0.0`）

---

## 功能概览（目标态）

| 能力 | 状态 |
|------|------|
| 项目骨架 / 健康检查 / SQLite 模型 | ✅ 阶段一 |
| 自然语言意图解析（API→Ollama→规则 + 确认） | ✅ 阶段二 |
| 公开真实数据源（ccgp + cebpub） | ✅ 阶段三 |
| 登录态真实数据源（Playwright 手动登录） | ✅ 阶段四 |
| 清洗 / 去重 / 增量 | ✅ 阶段三～五 |
| Word 报告 | ✅ 阶段六 |
| 定时任务 | ✅ 阶段七 |

本阶段**不返回伪造招标数据**。

---

## 环境要求

- Python ≥ 3.12
- Node.js ≥ 20 / npm
- Git
- 可选：Docker / Docker Compose

---

## 快速启动

### Windows PowerShell

```powershell
cd F:\feishu
copy .env.example .env
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

另开终端：

```powershell
cd F:\feishu\frontend
npm install
npm run dev
```

或使用脚本：

```powershell
.\scripts\start.ps1
```

### Linux / macOS

```bash
cp .env.example .env
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

```bash
cd frontend
npm install
npm run dev
```

或：

```bash
chmod +x scripts/start.sh
./scripts/start.sh
```

### 访问

- 前端：http://127.0.0.1:5173  
- 后端 API 文档：http://127.0.0.1:8000/docs  
- 健康检查：http://127.0.0.1:8000/api/health  

### Docker Compose

```bash
docker compose config   # 校验配置
docker compose up --build
```

---

## 测试

```bash
# 后端
cd backend
pytest

# 前端
cd frontend
npm run lint
npm run build
```

---

## 文档

- [详设文档](docs/详设文档.md)
- [开发计划](docs/开发计划.md)
- [开源调研与技术选型](docs/开源调研与技术选型.md)
- [LLM 双模式配置（API + Ollama）](docs/LLM双模式配置.md)
- [数据源说明](docs/数据源说明.md)
- [操作文档](docs/操作文档.md)
- [Demo 演示脚本](docs/Demo演示脚本.md)
- [测试报告](docs/测试报告.md)
- [验收清单](docs/验收清单.md)
- [AGENTS.md](AGENTS.md) — 开发约束

### 登录态初始化（阶段四）

```powershell
# 安装浏览器内核后：
cd backend
pip install playwright
playwright install chromium
python -m app.tools.login_init
# 或项目根目录：
.\scripts\login_source.ps1
```

---

## 安全提示

- 将 `.env.example` 复制为 `.env` 后填写密钥，**切勿提交 `.env`**
- `data/browser_states/` 登录状态禁止入库
- 不绕过验证码与网站访问控制

---

## 目录结构

```text
.
├── backend/          # FastAPI
├── frontend/         # React + Vite
├── data/             # 报告 / fixtures / browser_states
├── docs/             # 设计与计划
├── scripts/          # 启动与登录脚本
└── docker-compose.yml
```
