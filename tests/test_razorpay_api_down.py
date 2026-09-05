"""Tests for Razorpay API failure handling in the retry service.

Verifies:
- Exponential backoff timing (2s, 4s)
- Retry exhaustion → automatic escalation
- Partial success (fail first, succeed later)
- Audit trail for every retry event
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
import razorpay
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.enums import (
    InterventionStatus,
    InterventionType,
    PaymentMethod,
    TransactionStatus,
)
from app.models.interventions import Intervention
from app.models.transactions import Transaction
from app.services.razorpay_service import RazorpayService

pytestmark = pytest.mark.asyncio


def _make_failed_tx_and_intervention(db_session):
    """Helper: create a failed transaction + pending auto-retry intervention."""
    tx = Transaction(
        transaction_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        amount=Decimal("500.00"),
        currency="INR",
        status=TransactionStatus.FAILED,
        payment_method=PaymentMethod.UPI,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    intervention = Intervention(
        transaction_id=tx.transaction_id,
        intervention_type=InterventionType.AUTO_RETRY,
        max_retries=3,
        status=InterventionStatus.PENDING,
        created_at=datetime.now(timezone.utc),
    )
    return tx, intervention


async def test_retry_exhaustion_creates_escalation(db_session):
    """All 3 retry attempts fail → intervention FAILED + new MANUAL_ESCALATION created."""
    tx, intervention = _make_failed_tx_and_intervention(db_session)
    db_session.add_all([tx, intervention])
    await db_session.flush()

    service = RazorpayService()

    with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.side_effect = razorpay.errors.ServerError("500 Server Error")
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await service.execute_retry(tx, intervention, db_session)

    # Return value assertions
    assert result["status"] == "failed"
    assert result.get("escalated") is True
    assert result["attempts"] == 3

    # Intervention should be marked failed
    await db_session.refresh(intervention)
    assert intervention.status == InterventionStatus.FAILED

    # A new MANUAL_ESCALATION intervention should exist
    stmt = select(Intervention).where(
        Intervention.transaction_id == tx.transaction_id,
        Intervention.intervention_type == InterventionType.MANUAL_ESCALATION,
    )
    escalation = (await db_session.execute(stmt)).scalars().first()
    assert escalation is not None, "Escalation intervention was not created"

    # Audit log must have the exhaustion entry
    logs = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.transaction_id == tx.transaction_id)
        )
    ).scalars().all()
    event_types = [l.event_type for l in logs]
    assert "retry_exhausted_escalated" in event_types
    # Should also have per-attempt failure logs
    assert sum(1 for e in event_types if e == "retry_attempt_failed") == 2  # attempts 1 and 2


async def test_exponential_backoff_delays(db_session):
    """Backoff delays: attempt 1→2s, attempt 2→4s. No sleep after final attempt."""
    tx, intervention = _make_failed_tx_and_intervention(db_session)
    db_session.add_all([tx, intervention])
    await db_session.flush()

    service = RazorpayService()

    with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.side_effect = razorpay.errors.ServerError("500 Server Error")
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await service.execute_retry(tx, intervention, db_session)

    # Backoff: BASE_DELAY * 2^(attempt-1) → 2*1=2, 2*2=4
    calls = mock_sleep.call_args_list
    assert len(calls) == 2, f"Expected 2 sleep calls, got {len(calls)}"
    assert calls[0][0][0] == 2, f"First delay should be 2s, got {calls[0][0][0]}"
    assert calls[1][0][0] == 4, f"Second delay should be 4s, got {calls[1][0][0]}"


async def test_retry_succeeds_on_second_attempt(db_session):
    """First attempt fails, second succeeds → intervention SUCCEEDED, transaction SUCCESS."""
    tx, intervention = _make_failed_tx_and_intervention(db_session)
    db_session.add_all([tx, intervention])
    await db_session.flush()

    service = RazorpayService()

    with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.side_effect = [
            razorpay.errors.ServerError("500 Server Error"),
            {"id": "order_test_123", "status": "created"},
        ]
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await service.execute_retry(tx, intervention, db_session)

    assert result["status"] == "succeeded"
    assert result["attempts"] == 2
    assert result["order_id"] == "order_test_123"

    await db_session.refresh(intervention)
    assert intervention.status == InterventionStatus.SUCCEEDED

    await db_session.refresh(tx)
    assert tx.status == TransactionStatus.SUCCESS

    # Audit log should have both failure and success entries
    logs = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.transaction_id == tx.transaction_id)
        )
    ).scalars().all()
    event_types = [l.event_type for l in logs]
    assert "retry_attempt_failed" in event_types
    assert "retry_succeeded" in event_types


async def test_retry_succeeds_on_first_attempt(db_session):
    """First attempt succeeds immediately → no sleep, no failure logs."""
    tx, intervention = _make_failed_tx_and_intervention(db_session)
    db_session.add_all([tx, intervention])
    await db_session.flush()

    service = RazorpayService()

    with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = {"id": "order_instant_123", "status": "created"}
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await service.execute_retry(tx, intervention, db_session)

    assert result["status"] == "succeeded"
    assert result["attempts"] == 1
    mock_sleep.assert_not_called()

    logs = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.transaction_id == tx.transaction_id)
        )
    ).scalars().all()
    event_types = [l.event_type for l in logs]
    assert "retry_succeeded" in event_types
    assert "retry_attempt_failed" not in event_types
