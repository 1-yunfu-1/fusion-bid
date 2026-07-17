"""跨源去重与增量交付."""

from app.deduplication.engine import CandidateRecord, deduplicate_candidates
from app.deduplication.incremental import mark_delivered, plan_incremental_delivery

__all__ = [
    "CandidateRecord",
    "deduplicate_candidates",
    "plan_incremental_delivery",
    "mark_delivered",
]
