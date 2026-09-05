"""Tests for LLM failure handling in the classifier service.

Verifies that the classifier gracefully handles:
- Invalid JSON responses
- Invalid failure reasons
- API timeouts and rate limits
- Full classify_and_store with failed LLM (DB integration)
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from groq import APITimeoutError, RateLimitError, APIConnectionError, APIStatusError
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.enums import ActorType, FailureReasonType, TransactionStatus, PaymentMethod
from app.models.failure_classifications import FailureClassification
from app.models.transactions import Transaction
from app.services.classifier_service import ClassifierService

pytestmark = pytest.mark.asyncio


def _mock_groq_response(text: str) -> MagicMock:
    """Create a mock Groq chat completion response with given text content."""
    response = MagicMock()
    choice = MagicMock()
    choice.message.content = text
    response.choices = [choice]
    response.model_dump.return_value = {
        "id": "chatcmpl-mock",
        "choices": [{"message": {"content": text, "role": "assistant"}}],
    }
    return response


# ── Unit Tests (no DB) ──────────────────────────────────────────────


async def test_llm_returns_invalid_json():
    """Malformed JSON from the LLM -> graceful fallback to 'unknown'."""
    service = ClassifierService()
    mock_client = AsyncMock()
    mock_client.chat.completions.create.return_value = _mock_groq_response("this is not json at all")

    with patch.object(type(service), "client", new_callable=lambda: property(lambda self: mock_client)):
        result = await service.classify_failure({"amount": 100})

    assert result["predicted_reason"] == "unknown"
    assert result["confidence_score"] == 0.0
    assert result["reasoning"] == "classification_failed"


async def test_llm_returns_invalid_reason():
    """Valid JSON but unknown reason -> normalized to 'unknown'."""
    service = ClassifierService()
    mock_client = AsyncMock()
    mock_client.chat.completions.create.return_value = _mock_groq_response(
        '{"predicted_reason": "nonexistent_reason", "confidence_score": 0.9, "reasoning": "test"}'
    )

    with patch.object(type(service), "client", new_callable=lambda: property(lambda self: mock_client)):
        result = await service.classify_failure({"amount": 100})

    assert result["predicted_reason"] == "unknown"


async def test_llm_api_timeout():
    """Groq APITimeoutError -> graceful fallback, no crash."""
    service = ClassifierService()
    mock_client = AsyncMock()
    mock_client.chat.completions.create.side_effect = APITimeoutError(request=MagicMock())

    with patch.object(type(service), "client", new_callable=lambda: property(lambda self: mock_client)):
        result = await service.classify_failure({"amount": 100})

    assert result["predicted_reason"] == "unknown"
    assert result["confidence_score"] == 0.0


async def test_llm_rate_limit_error():
    """Groq RateLimitError -> graceful fallback, no crash."""
    service = ClassifierService()
    mock_client = AsyncMock()
    mock_client.chat.completions.create.side_effect = RateLimitError(
        message="Rate limited",
        response=MagicMock(status_code=429, headers={}),
        body={},
    )

    with patch.object(type(service), "client", new_callable=lambda: property(lambda self: mock_client)):
        result = await service.classify_failure({"amount": 100})

    assert result["predicted_reason"] == "unknown"
    assert result["confidence_score"] == 0.0


async def test_llm_api_connection_error():
    """Groq APIConnectionError -> graceful fallback, no crash."""
    service = ClassifierService()
    mock_client = AsyncMock()
    mock_client.chat.completions.create.side_effect = APIConnectionError(request=MagicMock())

    with patch.object(type(service), "client", new_callable=lambda: property(lambda self: mock_client)):
        result = await service.classify_failure({"amount": 100})

    assert result["predicted_reason"] == "unknown"
    assert result["confidence_score"] == 0.0


async def test_llm_api_status_error():
    """Groq APIStatusError (500, 503, etc.) -> graceful fallback, no crash."""
    service = ClassifierService()
    mock_client = AsyncMock()
    mock_client.chat.completions.create.side_effect = APIStatusError(
        message="Internal Server Error",
        response=MagicMock(status_code=500, headers={}),
        body={},
    )

    with patch.object(type(service), "client", new_callable=lambda: property(lambda self: mock_client)):
        result = await service.classify_failure({"amount": 100})

    assert result["predicted_reason"] == "unknown"
    assert result["confidence_score"] == 0.0


# ── Integration Test (DB required) ──────────────────────────────────


async def test_classify_and_store_with_failed_llm(db_session):
    """Full classify_and_store with a broken LLM -> stores 'unknown' + audit log."""
    tx = Transaction(
        transaction_id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        amount=Decimal("500.00"),
        currency="INR",
        status=TransactionStatus.FAILED,
        payment_method=PaymentMethod.UPI,
        failure_reason="bank_error",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(tx)
    await db_session.flush()

    service = ClassifierService()

    with patch.object(service, "classify_failure", new_callable=AsyncMock) as mock_classify:
        mock_classify.return_value = {
            "predicted_reason": "unknown",
            "confidence_score": 0.0,
            "reasoning": "classification_failed",
            "raw_response": None,
        }
        classification = await service.classify_and_store(tx.transaction_id, db_session)

    # Assert classification was stored
    assert classification is not None
    assert classification.predicted_reason == FailureReasonType.UNKNOWN
    assert classification.confidence_score == 0.0
    assert classification.transaction_id == tx.transaction_id

    # Assert audit log was created
    stmt = select(AuditLog).where(AuditLog.transaction_id == tx.transaction_id)
    logs = (await db_session.execute(stmt)).scalars().all()
    assert len(logs) >= 1

    audit = next(l for l in logs if l.event_type == "classification_completed")
    assert audit.actor == ActorType.AI
    assert audit.event_details["predicted_reason"] == "unknown"
    assert audit.event_details["confidence_score"] == 0.0
