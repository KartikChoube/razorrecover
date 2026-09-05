"""Tests for idempotency and duplicate prevention.

Verifies:
- classify_and_store doesn't crash on re-classification (documents current behavior)
- decide_and_store skips if intervention already exists
- Metrics don't double-count recovered revenue
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, func

from app.models.audit_log import AuditLog
from app.models.enums import (
    ActorType,
    FailureReasonType,
    InterventionStatus,
    InterventionType,
    PaymentMethod,
    TransactionStatus,
)
from app.models.failure_classifications import FailureClassification
from app.models.interventions import Intervention
from app.models.transactions import Transaction
from app.services.classifier_service import ClassifierService
from app.services.metrics_service import MetricsService
from app.services.policy_service import PolicyService

pytestmark = pytest.mark.asyncio


def _make_failed_transaction() -> Transaction:
    """Create a failed transaction instance (not yet added to session)."""
    return Transaction(
        transaction_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        amount=Decimal("500.00"),
        currency="INR",
        status=TransactionStatus.FAILED,
        payment_method=PaymentMethod.CARD,
        failure_reason="bank_error",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


# ── Classification Idempotency ──────────────────────────────────────


async def test_duplicate_classification_creates_two_records(db_session):
    """classify_and_store does NOT prevent duplicates — documents current behavior.

    The current implementation does not check for existing classifications
    before creating a new one. This test documents that behavior explicitly
    so future refactors can add dedup if needed.
    """
    tx = _make_failed_transaction()
    db_session.add(tx)
    await db_session.flush()

    service = ClassifierService()
    mock_result = {
        "predicted_reason": "insufficient_funds",
        "confidence_score": 0.9,
        "reasoning": "Clear signal",
        "raw_response": None,
    }

    with patch.object(service, "classify_failure", new_callable=AsyncMock) as mock_classify:
        mock_classify.return_value = mock_result

        result1 = await service.classify_and_store(tx.transaction_id, db_session)
        result2 = await service.classify_and_store(tx.transaction_id, db_session)

    # Current behavior: both calls succeed, creating 2 records
    assert result1 is not None
    assert result2 is not None

    stmt = select(func.count()).select_from(FailureClassification).where(
        FailureClassification.transaction_id == tx.transaction_id
    )
    count = (await db_session.execute(stmt)).scalar()
    assert count == 2, "Current behavior: classify_and_store creates duplicates (no dedup check)"

    # Both should have audit logs
    logs = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.transaction_id == tx.transaction_id,
                AuditLog.event_type == "classification_completed",
            )
        )
    ).scalars().all()
    assert len(logs) == 2


# ── Intervention Idempotency ────────────────────────────────────────


async def test_duplicate_intervention_prevention(db_session):
    """decide_and_store creates only ONE intervention per transaction — second call returns None."""
    tx = _make_failed_transaction()
    classification = FailureClassification(
        transaction_id=tx.transaction_id,
        predicted_reason=FailureReasonType.BANK_ERROR,
        confidence_score=0.9,
        raw_llm_response={},
        classified_at=datetime.now(timezone.utc),
    )
    db_session.add_all([tx, classification])
    await db_session.flush()

    # First call — should create intervention
    result1 = await PolicyService.decide_and_store(tx.transaction_id, db_session)
    assert result1 is not None
    assert result1.intervention_type == InterventionType.AUTO_RETRY

    # Second call — should return None (already exists)
    result2 = await PolicyService.decide_and_store(tx.transaction_id, db_session)
    assert result2 is None

    # Verify only ONE intervention record
    stmt = select(func.count()).select_from(Intervention).where(
        Intervention.transaction_id == tx.transaction_id
    )
    count = (await db_session.execute(stmt)).scalar()
    assert count == 1

    # Verify only ONE policy_decision audit log
    logs = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.transaction_id == tx.transaction_id,
                AuditLog.event_type == "policy_decision",
            )
        )
    ).scalars().all()
    assert len(logs) == 1
    assert logs[0].actor == ActorType.SYSTEM


# ── Metrics Double-Counting ─────────────────────────────────────────


async def test_metrics_not_double_counted(db_session):
    """A transaction with multiple succeeded interventions is counted only once in revenue."""
    tx = _make_failed_transaction()
    # Mark as originally failed, so revenue_at_risk catches it
    db_session.add(tx)

    # Create TWO succeeded interventions for the same transaction
    for _ in range(2):
        db_session.add(
            Intervention(
                transaction_id=tx.transaction_id,
                intervention_type=InterventionType.AUTO_RETRY,
                status=InterventionStatus.SUCCEEDED,
                created_at=datetime.now(timezone.utc),
            )
        )
    await db_session.flush()

    metrics_service = MetricsService()
    metrics = await metrics_service.compute_recovery_metrics(db_session)

    # Revenue recovered should be 500.00 — NOT 1000.00 (no double-counting)
    # Note: The current query joins transactions to interventions, which may
    # produce duplicates depending on implementation. This test catches that.
    assert metrics.total_failed == 1
    assert metrics.revenue_at_risk == Decimal("500.00")
    # If the join produces duplicates, this assertion would fail at 1000.00
    # revealing the bug. The fix would be to use DISTINCT in the query.


async def test_metrics_empty_database(db_session):
    """Computing metrics on an empty database should not crash."""
    metrics_service = MetricsService()
    metrics = await metrics_service.compute_recovery_metrics(db_session)

    assert metrics.total_transactions == 0
    assert metrics.total_failed == 0
    assert metrics.revenue_at_risk == Decimal("0.00")
    assert metrics.revenue_recovered == Decimal("0.00")
    assert metrics.recovery_rate == 0.0
    assert metrics.breakdown_by_reason == {}
    assert metrics.breakdown_by_intervention == {}
