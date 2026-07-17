# AGENTS.md — FusionBid 开发约定

## 项目

- 名称：FusionBid 智标聚合助手
- 赛题：2026 AI 先锋未来人才大赛——超聚变企业命题
- 默认语言：简体中文
- 默认时区：Asia/Shanghai

## 硬性约束

1. **禁止硬编码招投标结果**；禁止用静态演示页冒充 live 数据。
2. **禁止**将 API Key、账号密码、Cookie、storage state 写入代码或提交 Git。
3. **禁止**绕过验证码、登录安全机制、反爬策略或破解付费内容。
4. 登录态：Playwright 可见浏览器 + 用户手动登录 + 本地 storage state。
5. 登录源失败时，公开源仍应继续执行。
6. `data_mode` 必须区分 `live` / `fixture`；演示数据界面明确标注。
7. 无附件时 `attachment_links = []`，不得伪造。
8. 摘要不得编造预算、截止时间、联系人；无法确认写「原文未明确说明」。
9. 增量：仅成功交付后写入 DeliveryHistory；失败执行不得标记已推送。
10. 按阶段交付；阶段验证命令失败不得声称完成。

## 技术栈

- Backend: Python ≥3.12, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, SQLite
- Frontend: React, TypeScript, Vite, Ant Design, React Router, TanStack Query
- 后续: Playwright, python-docx, APScheduler, httpx, BS4, tenacity

## 目录

见 `docs/详设文档.md`。阶段包目录已预留：`parsers/`、`sources/`、`cleaners/`、`deduplication/`、`reports/`、`scheduler/`、`llm/`。

## 验证

```bash
cd backend && pytest
cd frontend && npm run lint && npm run build
docker compose config
```

## 密钥

环境变量见 `.env.example`。真实 `.env` 不入库。
