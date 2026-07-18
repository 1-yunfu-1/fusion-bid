# FusionBid 智标聚合助手

> 2026 AI 先锋未来人才大赛——超聚变企业命题  
> AI 招投标信息聚合工具：自然语言 → 多源采集 → 可验证详情 → 清洗去重 → Word 决策报告 → 定时增量

**默认语言**：简体中文 · **默认时区**：Asia/Shanghai  
**当前版本**：v1.0.0 · 比赛冲刺联调版

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
| 确认后自动执行首轮并保留结果 | ✅ |
| 详情证据链 / AI 决策分析 / 企业画像 | ✅ |

不返回伪造招标数据。

主演示路径固定为：**输入 → 解析 → 确认 → 自动首查 → 查看结果 → 下载 Word → 定时增量**。普通任务确认后必定首查；定时任务默认也先执行首轮，可在确认页关闭。

报告中的“采购人/招标人”和资格要求均保留原文标签、PDF 页码与原文证据。CEBPUB 使用旧公开接口发现公告，再以 `businessId` 映射当前 `ctbpsp.com` 详情；只有公告 ID 与标题校验通过的正文才进入抽取链路。遇到验证码或无法核验时标记为“待人工验证/仅元数据”，不会把门户通用页当成公告正文。

若自动化浏览器只显示空白外壳、但常用 Chrome 可以正常显示公告，可加载 `browser_extension/ctbpsp_capture`，直接从已渲染的 PDF.js 逐页采集文字层到本机 FusionBid；扩展不读取或上传 Cookie。也可在采集结果详情中导入下载的官方 PDF/HTML。系统不保存原始文件，只保存清洗正文、页码、SHA-256、导入时间和匹配依据；文件必须通过公告标题或项目编号校验。

抽取采用 **AI 优先 → 原文证据校验 → 规则兜底**。AI 建议只允许“建议参与 / 有条件参与 / 不建议参与 / 信息不足”，并引用证据 ID，不输出无依据的中标概率。任务列表的“重新采集未去重完整报告”是独立完整快照：重新抓取、保留每个来源记录、不读写 DeliveryHistory；达到每源 500 条上限会明确标记 `truncated=true`。

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

脚本不会强制结束占用 8000 端口的进程：当前版本 FusionBid 会直接打开；当前工作区旧版本会询问是否安全重启并先迁移数据库；其他进程或无法确认归属的实例只提示 PID 后退出。

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
alembic upgrade head
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

cd ..
docker compose config
```

当前仓库最近一次结果：后端 **97 passed**，前端 lint/build 通过，Docker Compose 配置通过。

---

## 文档

- [操作文档](docs/操作文档.md)
- [LLM 双模式配置](docs/LLM双模式配置.md)
- [数据源说明](docs/数据源说明.md)
- [Demo 演示脚本](docs/Demo演示脚本.md)
- [报名材料项目说明](docs/报名材料项目说明.md)
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
