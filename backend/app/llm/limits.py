"""Loop-local bounded concurrency for evidence extraction calls."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator
from weakref import WeakKeyDictionary

from app.core.config import get_settings

_semaphores: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    WeakKeyDictionary()
)


@asynccontextmanager
async def extraction_slot() -> AsyncIterator[None]:
    """Limit model-heavy announcement extraction without coupling test event loops."""
    loop = asyncio.get_running_loop()
    semaphore = _semaphores.get(loop)
    if semaphore is None:
        semaphore = asyncio.Semaphore(get_settings().llm_extraction_concurrency)
        _semaphores[loop] = semaphore
    async with semaphore:
        yield
