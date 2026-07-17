"""数据源适配器包."""

from app.sources.registry import build_sources, get_source

__all__ = ["build_sources", "get_source"]
