"""Recovery metrics computation and storage.

Aggregates transaction, classification, and intervention data into
actionable recovery metrics for the dashboard and reporting.
"""
from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import InterventionStatus, TransactionStatus
from app.models.failure_classifications import FailureClassification
from app.models.interventions import Intervention
from app.models.recovery_metrics import RecoveryMetrics
from app.models.transactions import Transaction

logger = logging.getLogger(__name__)


class MetricsService:
    """Service for computing and managing recovery metrics."""

    async def compute_recovery_metrics(
        self, session: AsyncSession, batch_id: UUID | None = None
    ) -> RecoveryMetrics:
        """Computes and stores recovery metrics.

        Args:
            session: SQLAlchemy AsyncSession.
            batch_id: Optional batch ID for tracking metrics groups.

        Returns:
            The computed RecoveryMetrics instance.
        """
        logger.info("Computing recovery metrics")
        batch_id = batch_id or uuid.uuid4()

        try:
            # 1. Total transactions
            stmt_total_tx = select(func.count()).select_from(Transaction)
            total_tx = (await session.execute(stmt_total_tx)).scalar() or 0

            # 4. Revenue recovered (calculate first to establish baseline revenue at risk)
            stmt_revenue_recovered = (
                select(func.coalesce(func.sum(Transaction.amount), Decimal("0.0")))
                .select_from(Transaction)
                .join(Intervention, Intervention.transaction_id == Transaction.transaction_id)
                .where(Intervention.status == InterventionStatus.SUCCEEDED)
            )
            revenue_recovered = (
                await session.execute(stmt_revenue_recovered)
            ).scalar()
            if revenue_recovered is None:
                revenue_recovered = Decimal("0.0")

            stmt_count_recovered = (
                select(func.count(Transaction.transaction_id))
                .select_from(Transaction)
                .join(Intervention, Intervention.transaction_id == Transaction.transaction_id)
                .where(Intervention.status == InterventionStatus.SUCCEEDED)
            )
            count_recovered = (await session.execute(stmt_count_recovered)).scalar() or 0

            # 2. Total failed (currently failed + successfully recovered)
            stmt_currently_failed = select(func.count()).select_from(Transaction).where(
                Transaction.status == TransactionStatus.FAILED
            )
            currently_failed = (await session.execute(stmt_currently_failed)).scalar() or 0
            total_failed = currently_failed + count_recovered

            # 3. Revenue at risk (baseline failed amounts = currently failed + recovered)
            stmt_currently_failed_amount = select(
                func.coalesce(func.sum(Transaction.amount), Decimal("0.0"))
            ).where(Transaction.status == TransactionStatus.FAILED)
            currently_failed_amount = (await session.execute(stmt_currently_failed_amount)).scalar()
            if currently_failed_amount is None:
                currently_failed_amount = Decimal("0.0")
            revenue_at_risk = currently_failed_amount + revenue_recovered

            # 5. Recovery rate (guaranteed null-safe, zero division protected, bounded 0-100)
            if revenue_at_risk and revenue_at_risk > Decimal("0.0") and revenue_recovered:
                recovery_rate = float((revenue_recovered / revenue_at_risk) * 100)
                recovery_rate = max(0.0, min(100.0, recovery_rate))
            else:
                recovery_rate = 0.0

            # 6. Breakdown by reason (all classified failed transactions, including recovered)
            stmt_breakdown_reason = (
                select(
                    FailureClassification.predicted_reason,
                    func.count().label("count"),
                    func.coalesce(func.sum(Transaction.amount), Decimal("0.0")).label("amount"),
                )
                .select_from(Transaction)
                .join(
                    FailureClassification,
                    FailureClassification.transaction_id == Transaction.transaction_id,
                )
                .group_by(FailureClassification.predicted_reason)
            )
            reason_results = await session.execute(stmt_breakdown_reason)
            breakdown_by_reason: dict[str, Any] = {}
            for row in reason_results.all():
                if row.predicted_reason is not None:
                    reason_key = (
                        row.predicted_reason.value
                        if hasattr(row.predicted_reason, "value")
                        else str(row.predicted_reason)
                    )
                else:
                    reason_key = "unknown"
                breakdown_by_reason[reason_key] = {
                    "count": row.count or 0,
                    "amount": float(row.amount or 0.0),
                }

            # 7. Breakdown by intervention
            stmt_breakdown_intervention = (
                select(
                    Intervention.intervention_type,
                    func.count().label("count"),
                    func.sum(
                        case((Intervention.status == InterventionStatus.SUCCEEDED, 1), else_=0)
                    ).label("succeeded"),
                )
                .select_from(Intervention)
                .group_by(Intervention.intervention_type)
            )
            intervention_results = await session.execute(stmt_breakdown_intervention)
            breakdown_by_intervention: dict[str, Any] = {}
            for row in intervention_results.all():
                if row.intervention_type is not None:
                    type_key = (
                        row.intervention_type.value
                        if hasattr(row.intervention_type, "value")
                        else str(row.intervention_type)
                    )
                else:
                    type_key = "unknown"
                cnt = row.count or 0
                succ = row.succeeded or 0
                success_rate = float((succ / cnt) * 100) if cnt > 0 else 0.0
                breakdown_by_intervention[type_key] = {
                    "count": cnt,
                    "succeeded": succ,
                    "success_rate": max(0.0, min(100.0, success_rate)),
                }

            metrics = RecoveryMetrics(
                batch_id=batch_id,
                total_transactions=total_tx,
                total_failed=total_failed,
                revenue_at_risk=revenue_at_risk,
                revenue_recovered=revenue_recovered,
                recovery_rate=recovery_rate,
                breakdown_by_reason=breakdown_by_reason,
                breakdown_by_intervention=breakdown_by_intervention,
            )
            session.add(metrics)
            await session.flush()
            logger.info("Recovery metrics computed successfully: recovered=₹%s (%.2f%%)", revenue_recovered, recovery_rate)
            return metrics

        except Exception as e:
            logger.exception("Exception occurred during recovery metrics computation: %s", e)
            raise

    async def get_latest_metrics(self, session: AsyncSession) -> RecoveryMetrics | None:
        """Retrieves the most recently computed metrics.

        Args:
            session: SQLAlchemy AsyncSession.

        Returns:
            The latest RecoveryMetrics instance or None if not available.
        """
        stmt = select(RecoveryMetrics).order_by(RecoveryMetrics.computed_at.desc()).limit(1)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
