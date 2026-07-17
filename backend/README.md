# FusionBid Backend

Python FastAPI 后端。阶段一提供健康检查、数据模型与 SQLite 初始化。

## 本地开发

```bash
cd backend
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
# source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 测试

```bash
pytest
```

## 迁移

```bash
alembic upgrade head
```
