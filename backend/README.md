# FusionBid Backend

Python FastAPI 后端。提供意图确认、任务执行、多源采集、去重增量、Word 报告与定时调度 API。

## 本地开发

```bash
cd backend
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
# source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

## 测试

```bash
pytest
```

## 迁移

```bash
alembic upgrade head
```
