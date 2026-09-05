"""Interventions router for managing and processing policy decisions and retries.

Orchestrates the deterministic policy engine, Razorpay retry execution,
and Redis retry queue. Every action is logged to the audit trail.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.enums import ActorType, InterventionStatus, InterventionType
from app.models import AuditLog, FailureClassification, Intervention, Transaction
from app.schemas.interventions import (
    BatchInterventionResponse,
    InterventionResponse,
    PolicySummary,
    ProcessDueRetriesResponse,
    RetryQueueStatus,
)
from app.services.policy_service import PolicyService, RETRY_DELAY_SECONDS
from app.services.razorpay_service import RazorpayService
from app.services.retry_queue import RetryQueue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/interventions", tags=["Interventions"])

policy_service = PolicyService()
razorpay_service = RazorpayService()
retry_queue = RetryQueue()


@router.post("/process-batch", response_model=BatchInterventionResponse)
async def process_batch(
    limit: int = Query(default=200, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> BatchInterventionResponse:
    """Process all classified transactions needing interventions.

    Fetches failed transactions that have a classification but no intervention,
    applies the deterministic policy engine, and enqueues auto-retries to Redis.
    """
    try:
        stmt = (
            select(Transaction)
            .join(
                FailureClassification,
                Transaction.transaction_id == FailureClassification.transaction_id,
            )
            .outerjoin(
                Intervention,
                Transaction.transaction_id == Intervention.transaction_id,
            )
            .where(Intervention.id.is_(None))
            .limit(limit)
        )
        result = await session.execute(stmt)
        transactions = result.scalars().all()

        counts = {
            "processed": 0,
            "auto_retry": 0,
            "customer_notification": 0,
            "manual_escalation": 0,
            "no_action": 0,
            "errors": 0,
        }

        for tx in transactions:
            try:
                intervention = await policy_service.decide_and_store(
                    tx.transaction_id, session
                )
                if not intervention:
                    continue

                counts["processed"] += 1
                itype = intervention.intervention_type

                if itype == InterventionType.AUTO_RETRY:
                    counts["auto_retry"] += 1
                    execute_at = datetime.now(timezone.utc) + timedelta(
                        seconds=RETRY_DELAY_SECONDS
                    )
                    await retry_queue.enqueue(
                        tx.transaction_id, intervention.id, execute_at
                    )
                    session.add(
                        AuditLog(
                            transaction_id=tx.transaction_id,
                            event_type="retry_enqueued",
                            actor=ActorType.SYSTEM,
                            event_details={
                                "intervention_id": intervention.id,
                                "delay_seconds": RETRY_DELAY_SECONDS,
                                "execute_at": execute_at.isoformat(),
                            },
                        )
                    )
                elif itype == InterventionType.CUSTOMER_NOTIFICATION:
                    counts["customer_notification"] += 1
                elif itype == InterventionType.MANUAL_ESCALATION:
                    counts["manual_escalation"] += 1
                elif itype == InterventionType.NO_ACTION:
                    counts["no_action"] += 1

            except Exception as e:
                logger.error(
                    "Error processing transaction %s: %s", tx.transaction_id, e
                )
                counts["errors"] += 1

        await session.commit()
        return BatchInterventionResponse(**counts)

    except Exception as e:
        await session.rollback()
        logger.exception("Failed to process intervention batch")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error while processing batch",
        )


@router.post("/process-due-retries", response_model=ProcessDueRetriesResponse)
async def process_due_retries(
    session: AsyncSession = Depends(get_session),
) -> ProcessDueRetriesResponse:
    """Process scheduled retries whose execution time has arrived.

    Pulls due jobs from the Redis sorted set, executes each retry via
    the Razorpay service, and re-enqueues if retries remain.
    """
    try:
        jobs = await retry_queue.get_due_jobs()

        # For demo/batch execution: also retrieve any pending AUTO_RETRY interventions in the DB
        seen_tx_ids = {UUID(j["transaction_id"]) for j in jobs if j.get("transaction_id")}
        pending_stmt = (
            select(Intervention)
            .where(
                Intervention.intervention_type == InterventionType.AUTO_RETRY,
                Intervention.status == InterventionStatus.PENDING,
            )
        )
        pending_interventions = (await session.execute(pending_stmt)).scalars().all()
        for inv in pending_interventions:
            if inv.transaction_id not in seen_tx_ids:
                jobs.append({
                    "transaction_id": str(inv.transaction_id),
                    "intervention_id": inv.id,
                    "_raw_payload": None,
                })
                seen_tx_ids.add(inv.transaction_id)

        counts = {"processed": 0, "succeeded": 0, "failed": 0, "escalated": 0}

        for job in jobs:
            try:
                tx_id_str = job.get("transaction_id")
                inv_id = job.get("intervention_id")
                raw_payload = job.get("_raw_payload")

                if not tx_id_str or not inv_id:
                    if raw_payload:
                        await retry_queue.remove_job(raw_payload)
                    continue

                tx_id = UUID(tx_id_str)

                # Fetch transaction and intervention
                tx_result = await session.execute(
                    select(Transaction).where(Transaction.transaction_id == tx_id)
                )
                transaction = tx_result.scalar_one_or_none()

                inv_result = await session.execute(
                    select(Intervention).where(Intervention.id == inv_id)
                )
                intervention = inv_result.scalar_one_or_none()

                if not transaction or not intervention:
                    if raw_payload:
                        await retry_queue.remove_job(raw_payload)
                    continue

                # Execute the retry
                retry_result = await razorpay_service.execute_retry(
                    transaction, intervention, session
                )
                counts["processed"] += 1

                # Remove the completed job from Redis
                if raw_payload:
                    await retry_queue.remove_job(raw_payload)

                if retry_result.get("status") == "succeeded":
                    counts["succeeded"] += 1
                elif retry_result.get("escalated"):
                    counts["escalated"] += 1
                else:
                    # Retry failed but not escalated — re-enqueue if retries remain
                    max_r = intervention.max_retries or 0
                    if intervention.retry_count < max_r:
                        counts["failed"] += 1
                        execute_at = datetime.now(timezone.utc) + timedelta(
                            seconds=RETRY_DELAY_SECONDS
                        )
                        await retry_queue.enqueue(tx_id, inv_id, execute_at)
                        session.add(
                            AuditLog(
                                transaction_id=tx_id,
                                event_type="retry_requeued",
                                actor=ActorType.SYSTEM,
                                event_details={
                                    "intervention_id": inv_id,
                                    "retry_count": intervention.retry_count,
                                    "max_retries": max_r,
                                },
                            )
                        )
                    else:
                        counts["escalated"] += 1

            except Exception as e:
                logger.error("Error processing retry job: %s", e)

        await session.commit()
        return ProcessDueRetriesResponse(**counts)

    except Exception as e:
        await session.rollback()
        logger.exception("Failed to process due retries")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error processing retries",
        )


@router.get("/queue-status", response_model=RetryQueueStatus)
async def get_queue_status() -> RetryQueueStatus:
    """View the current Redis retry queue status."""
    try:
        size = await retry_queue.get_queue_size()
        pending = await retry_queue.get_pending_jobs()
        return RetryQueueStatus(queue_size=size, pending_jobs=pending)
    except Exception as e:
        logger.error("Failed to get queue status: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve queue status",
        )


@router.get("/policy-rules", response_model=PolicySummary)
async def get_policy_rules() -> PolicySummary:
    """View the deterministic policy engine rule configuration."""
    rules = PolicyService.get_policy_summary()
    return PolicySummary(rules=rules)


@router.get("/{intervention_id}", response_model=InterventionResponse)
async def get_intervention(
    intervention_id: int,
    session: AsyncSession = Depends(get_session),
) -> InterventionResponse:
    """Get a single intervention by ID."""
    stmt = select(Intervention).where(Intervention.id == intervention_id)
    result = await session.execute(stmt)
    intervention = result.scalar_one_or_none()

    if not intervention:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Intervention not found",
        )

    return intervention


@router.get("/", response_model=list[InterventionResponse])
async def list_interventions(
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    intervention_status: InterventionStatus | None = Query(default=None, alias="status"),
    intervention_type: InterventionType | None = None,
    session: AsyncSession = Depends(get_session),
) -> list:
    """List interventions with optional filters."""
    stmt = select(Intervention)

    if intervention_status:
        stmt = stmt.where(Intervention.status == intervention_status)
    if intervention_type:
        stmt = stmt.where(Intervention.intervention_type == intervention_type)

    stmt = stmt.order_by(Intervention.created_at.desc()).offset(offset).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())
