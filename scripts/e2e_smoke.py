#!/usr/bin/env python3
"""离线端到端冒烟：意图解析确认 + Word 报告（不访问真实招标站）.

用法（在 backend 目录，已安装依赖）:
  python ../scripts/e2e_smoke.py
  或
  python scripts/e2e_smoke.py   # 从项目根需配置 PYTHONPATH
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from httpx import ASGITransport, AsyncClient  # noqa: E402

# 测试库
import os

os.environ["APP_ENV"] = "test"
os.environ["APP_DEBUG"] = "false"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["LLM_ENABLED"] = "false"

from app.core.config import get_settings  # noqa: E402
from app.core.database import Base, get_db, init_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.reports.word_report import ReportContext, generate_report_file  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

TZ = ZoneInfo("Asia/Shanghai")
REF = "2026-07-17T08:00:00+08:00"

OFFICIAL_QUERIES = [
    "最近1个月的安徽省区域内的服务器招标信息都有哪些",
    "2026年3月份的上海区域内的充电桩招标信息都有哪些",
    "最近3个月的上海区域内的充电桩招标信息都有哪些，请汇总后每天9:00发送给我",
]


async def main() -> int:
    get_settings.cache_clear()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    app = create_app()

    async def _override():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override

    print("=== FusionBid E2E Smoke (offline) ===\n")
    ok = 0
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        h = await client.get("/api/health")
        print(f"[health] {h.status_code} {h.json().get('status')} phase={h.json().get('phase')}")
        if h.status_code != 200 or not h.json().get("database_ok"):
            print("FAIL health")
            return 1
        ok += 1

        for i, q in enumerate(OFFICIAL_QUERIES, 1):
            print(f"\n--- Case {i}: {q[:40]}...")
            pr = await client.post(
                "/api/parse",
                json={"query": q, "prefer_llm": False, "reference_time": REF},
            )
            if pr.status_code != 200:
                print(f"FAIL parse {pr.status_code} {pr.text}")
                return 1
            body = pr.json()
            intent = body["intent"]
            print(
                f"  parser={body['parser_used']} "
                f"kw={intent['keywords']} regions={intent['regions']} "
                f"range={intent['date_range']} "
                f"schedule={intent['schedule']} immediate={intent['execute_immediately']}"
            )
            # 补全可能的校验缺口以便确认
            if not intent["keywords"]:
                intent["keywords"] = ["招标"]
            if not intent["regions"]:
                intent["regions"] = ["上海市"]
            if not intent["date_range"].get("start_date"):
                intent["date_range"]["start_date"] = "2026-04-17"
                intent["date_range"]["end_date"] = "2026-07-17"

            cr = await client.post("/api/parse/confirm", json={"intent": intent})
            if cr.status_code != 200:
                print(f"FAIL confirm {cr.status_code} {cr.text[:300]}")
                return 1
            print(f"  task_id={cr.json()['task_id'][:8]}… status={cr.json()['status']}")
            ok += 1

        # Word 报告（模拟一次增量结果）
        settings = get_settings()
        reports_dir = ROOT / "data" / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        path = generate_report_file(
            ReportContext(
                original_query=OFFICIAL_QUERIES[0],
                generated_at=datetime.now(TZ),
                execute_type="立即执行",
                data_mode="实时数据",
                keywords=["服务器"],
                regions=["安徽省"],
                start_date="2026-06-17",
                end_date="2026-07-17",
                sources=["ccgp", "cebpub"],
                raw_result_count=0,
                items=[],
                extra_notes=["E2E 冒烟：未访问真实站点，结果列表为空属预期"],
            ),
            reports_dir=reports_dir,
        )
        print(f"\n[report] wrote {path.name} ({path.stat().st_size} bytes)")
        if not path.is_file() or not path.name.endswith(".docx"):
            print("FAIL report")
            return 1
        ok += 1

    await engine.dispose()
    print(f"\n=== PASS ({ok} checks) ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
