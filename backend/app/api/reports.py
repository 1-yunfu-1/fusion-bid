"""报告列表与下载 API."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.models.execution import TaskExecution
from app.models.task import SearchTask

router = APIRouter(prefix="/reports", tags=["reports"])

_SAFE_NAME = re.compile(r"^[^/\\]+\.docx$")


def _reports_dir() -> Path:
    return get_settings().reports_dir.resolve()


def _safe_resolve(name: str) -> Path:
    """防止路径穿越."""
    if not name or not _SAFE_NAME.match(name):
        # 允许中文文件名：仅禁止分隔符
        if "/" in name or "\\" in name or ".." in name or not name.endswith(".docx"):
            raise HTTPException(status_code=400, detail="非法文件名")
    base = _reports_dir()
    path = (base / name).resolve()
    if not str(path).startswith(str(base)):
        raise HTTPException(status_code=400, detail="非法路径")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="报告不存在")
    return path


@router.get("")
async def list_reports(db: AsyncSession = Depends(get_db)) -> dict:
    """合并磁盘报告与执行记录."""
    base = _reports_dir()
    base.mkdir(parents=True, exist_ok=True)
    disk_files = sorted(base.glob("*.docx"), key=lambda p: p.stat().st_mtime, reverse=True)

    exec_rows = (
        await db.execute(
            select(TaskExecution, SearchTask)
            .join(SearchTask, SearchTask.id == TaskExecution.task_id)
            .where(TaskExecution.report_path.is_not(None))
            .order_by(TaskExecution.created_at.desc())
        )
    ).all()
    by_name: dict[str, dict] = {}
    for ex, task in exec_rows:
        if not ex.report_path:
            continue
        name = Path(ex.report_path).name
        by_name[name] = {
            "filename": name,
            "path": ex.report_path,
            "execution_id": ex.id,
            "task_id": task.id,
            "original_query": task.original_query,
            "status": ex.status,
            "incremental_count": ex.incremental_count,
            "finished_at": ex.finished_at.isoformat() if ex.finished_at else None,
            "exists": (base / name).is_file(),
        }

    items = []
    seen = set()
    for f in disk_files:
        seen.add(f.name)
        meta = by_name.get(f.name, {})
        items.append(
            {
                "filename": f.name,
                "size": f.stat().st_size,
                "modified_at": f.stat().st_mtime,
                "execution_id": meta.get("execution_id"),
                "task_id": meta.get("task_id"),
                "original_query": meta.get("original_query"),
                "status": meta.get("status"),
                "incremental_count": meta.get("incremental_count"),
                "finished_at": meta.get("finished_at"),
                "exists": True,
            }
        )
    # 库中有记录但文件丢失
    for name, meta in by_name.items():
        if name not in seen:
            items.append({**meta, "size": 0, "modified_at": None, "exists": False})

    return {"items": items, "total": len(items), "reports_dir": str(base)}


@router.get("/download/{filename}")
async def download_report(filename: str) -> FileResponse:
    path = _safe_resolve(filename)
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=path.name,
    )


@router.get("/by-execution/{execution_id}")
async def report_by_execution(
    execution_id: str, db: AsyncSession = Depends(get_db)
) -> dict:
    ex = await db.get(TaskExecution, execution_id)
    if not ex or not ex.report_path:
        raise HTTPException(status_code=404, detail="该执行无报告")
    name = Path(ex.report_path).name
    exists = (_reports_dir() / name).is_file()
    return {
        "execution_id": ex.id,
        "filename": name,
        "report_path": ex.report_path,
        "exists": exists,
        "download_url": f"/api/reports/download/{name}" if exists else None,
    }
