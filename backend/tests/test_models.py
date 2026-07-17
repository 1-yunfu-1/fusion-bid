"""数据模型元数据与基础结构测试."""

from app.core.database import Base
from app import models  # noqa: F401


def test_tables_registered():
    names = set(Base.metadata.tables.keys())
    assert "search_tasks" in names
    assert "task_executions" in names
    assert "tender_announcements" in names
    assert "delivery_histories" in names


def test_announcement_has_data_mode():
    table = Base.metadata.tables["tender_announcements"]
    assert "data_mode" in table.c
    assert "source_url" in table.c
    assert "attachment_links" in table.c
    assert "content_hash" in table.c


def test_search_task_schedule_fields():
    table = Base.metadata.tables["search_tasks"]
    for col in (
        "original_query",
        "parsed_intent",
        "keywords",
        "regions",
        "start_date",
        "end_date",
        "execute_immediately",
        "schedule_enabled",
        "schedule_type",
        "execute_time",
        "timezone",
        "status",
    ):
        assert col in table.c
