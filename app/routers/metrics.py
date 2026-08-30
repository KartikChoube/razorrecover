"""Recovery metrics and reporting endpoints."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/metrics", tags=["Metrics"])

@router.get("/")
async def get_metrics() -> dict:
    """Get metrics."""
    return {"status": "metrics router active"}
