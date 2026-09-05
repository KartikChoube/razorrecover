"""Recovery metrics and reporting endpoints."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.recovery_metrics import RecoveryMetrics
from app.models.transactions import Transaction
from app.schemas.recovery_metrics import RecoveryMetricsResponse
from app.services.metrics_service import MetricsService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/metrics", tags=["Metrics"])
metrics_service = MetricsService()


@router.post("/compute", response_model=RecoveryMetricsResponse)
async def compute_metrics(
    session: AsyncSession = Depends(get_session)
) -> Any:
    """Triggers computation and stores results.

    Returns the freshly computed metrics.
    """
    try:
        metrics = await metrics_service.compute_recovery_metrics(session=session)
        await session.commit()
        return metrics
    except Exception as e:
        logger.exception("Failed to compute metrics")
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to compute metrics.",
        ) from e


@router.get("/summary", response_model=RecoveryMetricsResponse)
async def get_metrics_summary(
    session: AsyncSession = Depends(get_session)
) -> Any:
    """Returns the most recently computed metrics.

    If none exist, attempts to auto-compute first.
    Returns 404 if no transactions exist at all.
    """
    try:
        metrics = await metrics_service.get_latest_metrics(session=session)
        if not metrics:
            # Check if we have any transactions
            tx_count = (await session.execute(select(func.count()).select_from(Transaction))).scalar() or 0
            if tx_count == 0:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No transactions exist to compute metrics from.",
                )
            # Auto-compute first time
            metrics = await metrics_service.compute_recovery_metrics(session=session)
            await session.commit()
            
        return metrics
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get metrics summary: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get metrics summary: {e}",
        ) from e


@router.get("/history", response_model=list[RecoveryMetricsResponse])
async def get_metrics_history(
    limit: int = Query(default=10, le=100),
    session: AsyncSession = Depends(get_session)
) -> Any:
    """Returns recent metrics history ordered by computed_at DESC."""
    stmt = (
        select(RecoveryMetrics)
        .order_by(RecoveryMetrics.computed_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
