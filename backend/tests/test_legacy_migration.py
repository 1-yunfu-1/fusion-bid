"""Regression coverage for databases created before Alembic versioning."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def _run_alembic(backend_dir: Path, database: Path, revision: str) -> None:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite+aiosqlite:///{database.as_posix()}"
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=backend_dir,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def test_unversioned_legacy_database_is_adopted_without_recreating_tables(
    tmp_path: Path,
) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    database = tmp_path / "legacy.db"
    _run_alembic(backend_dir, database, "20260717_0004")

    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO search_tasks ("
            "id, original_query, execute_immediately, schedule_enabled, timezone, status"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            ("legacy-task", "保留的历史任务", 1, 0, "Asia/Shanghai", "done"),
        )
        connection.execute("DELETE FROM alembic_version")
        connection.commit()

    _run_alembic(backend_dir, database, "head")

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT original_query FROM search_tasks").fetchone() == (
            "保留的历史任务",
        )
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "20260719_0008",
        )
        announcement_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(tender_announcements)")
        }
        execution_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(task_executions)")
        }
        assert {
            "detail_url",
            "extraction_version",
            "analysis_data",
            "lifecycle_stage",
            "procurement_method",
            "document_hash",
            "extraction_fingerprint",
        } <= announcement_columns
        assert {
            "report_mode",
            "truncated",
            "detail_full_count",
            "crawl_diagnostics",
        } <= execution_columns
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='company_profiles'"
        ).fetchone()
