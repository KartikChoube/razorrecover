"""Tests for policy engine boundary conditions.

Pure unit tests — no database required. Uses mock objects to verify
the deterministic policy engine produces correct outputs at exact
threshold boundaries.

Thresholds under test:
    LOW_AMOUNT_THRESHOLD  = Decimal("1000.00")   (strict less-than)
    MID_AMOUNT_THRESHOLD  = Decimal("10000.00")  (strict greater-than)
    CONFIDENCE_THRESHOLD  = 0.5                   (strict less-than)
"""
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.models.enums import FailureReasonType, InterventionType
from app.services.policy_service import PolicyService


def _tx(amount: Decimal) -> MagicMock:
    """Create a mock Transaction with the given amount."""
    tx = MagicMock()
    tx.amount = amount
    return tx


def _cls(reason: FailureReasonType, confidence: float) -> MagicMock:
    """Create a mock FailureClassification."""
    c = MagicMock()
    c.predicted_reason = reason
    c.confidence_score = confidence
    return c


# ── Confidence Threshold Tests ──────────────────────────────────────


class TestConfidenceThreshold:
    """Gate 1: confidence < 0.5 → MANUAL_ESCALATION regardless of everything."""

    def test_below_threshold(self):
        """confidence=0.49 → MANUAL_ESCALATION."""
        decision = PolicyService.decide_intervention(
            _tx(Decimal("500.00")),
            _cls(FailureReasonType.BANK_ERROR, 0.49),
        )
        assert decision.intervention_type == InterventionType.MANUAL_ESCALATION

    def test_at_threshold(self):
        """confidence=0.5 (NOT strictly < 0.5) → proceeds to rules, NOT escalation."""
        decision = PolicyService.decide_intervention(
            _tx(Decimal("500.00")),
            _cls(FailureReasonType.BANK_ERROR, 0.5),
        )
        assert decision.intervention_type != InterventionType.MANUAL_ESCALATION

    def test_above_threshold(self):
        """confidence=0.51 → proceeds to rules."""
        decision = PolicyService.decide_intervention(
            _tx(Decimal("500.00")),
            _cls(FailureReasonType.BANK_ERROR, 0.51),
        )
        assert decision.intervention_type != InterventionType.MANUAL_ESCALATION

    def test_zero_confidence(self):
        """confidence=0.0 → MANUAL_ESCALATION."""
        decision = PolicyService.decide_intervention(
            _tx(Decimal("100.00")),
            _cls(FailureReasonType.BANK_ERROR, 0.0),
        )
        assert decision.intervention_type == InterventionType.MANUAL_ESCALATION


# ── Amount Threshold Tests ──────────────────────────────────────────


class TestAmountThresholds:
    """Gate 2: amount > 10000 → MANUAL_ESCALATION.
    Rules split at amount < 1000 vs 1000-10000.
    """

    def test_just_above_mid(self):
        """amount=10000.01 → MANUAL_ESCALATION (> MID_AMOUNT)."""
        decision = PolicyService.decide_intervention(
            _tx(Decimal("10000.01")),
            _cls(FailureReasonType.BANK_ERROR, 0.8),
        )
        assert decision.intervention_type == InterventionType.MANUAL_ESCALATION

    def test_exactly_at_mid(self):
        """amount=10000.00 (NOT > 10000) → proceeds to rules, not escalated."""
        decision = PolicyService.decide_intervention(
            _tx(Decimal("10000.00")),
            _cls(FailureReasonType.BANK_ERROR, 0.8),
        )
        # Should be AUTO_RETRY with max_retries=2 (mid-range bank_error)
        assert decision.intervention_type == InterventionType.AUTO_RETRY
        assert decision.max_retries == 2

    def test_just_below_low(self):
        """amount=999.99 (< LOW_AMOUNT) → low-range rule applies."""
        decision = PolicyService.decide_intervention(
            _tx(Decimal("999.99")),
            _cls(FailureReasonType.BANK_ERROR, 0.8),
        )
        assert decision.intervention_type == InterventionType.AUTO_RETRY
        assert decision.max_retries == 3  # LOW range: max 3 retries

    def test_exactly_at_low(self):
        """amount=1000.00 (NOT < 1000) → mid-range rule applies."""
        decision = PolicyService.decide_intervention(
            _tx(Decimal("1000.00")),
            _cls(FailureReasonType.BANK_ERROR, 0.8),
        )
        assert decision.intervention_type == InterventionType.AUTO_RETRY
        assert decision.max_retries == 2  # MID range: max 2 retries


# ── Failure Reason Tests ────────────────────────────────────────────


class TestExpiredCard:
    """Expired card → CUSTOMER_NOTIFICATION regardless of amount."""

    @pytest.mark.parametrize("amount", ["100.00", "999.99", "5000.00", "9999.99"])
    def test_any_amount(self, amount):
        decision = PolicyService.decide_intervention(
            _tx(Decimal(amount)),
            _cls(FailureReasonType.EXPIRED_CARD, 0.9),
        )
        assert decision.intervention_type == InterventionType.CUSTOMER_NOTIFICATION


class TestInsufficientFunds:
    """Insufficient funds → CUSTOMER_NOTIFICATION for all amounts under 10K."""

    @pytest.mark.parametrize("amount", ["100.00", "999.99", "1000.00", "5000.00", "9999.99"])
    def test_notification_under_10k(self, amount):
        decision = PolicyService.decide_intervention(
            _tx(Decimal(amount)),
            _cls(FailureReasonType.INSUFFICIENT_FUNDS, 0.8),
        )
        assert decision.intervention_type == InterventionType.CUSTOMER_NOTIFICATION


class TestBankError:
    """Bank error → AUTO_RETRY with amount-dependent max retries."""

    def test_low_amount_max_3_retries(self):
        decision = PolicyService.decide_intervention(
            _tx(Decimal("500.00")),
            _cls(FailureReasonType.BANK_ERROR, 0.8),
        )
        assert decision.intervention_type == InterventionType.AUTO_RETRY
        assert decision.max_retries == 3

    def test_mid_amount_max_2_retries(self):
        decision = PolicyService.decide_intervention(
            _tx(Decimal("5000.00")),
            _cls(FailureReasonType.BANK_ERROR, 0.8),
        )
        assert decision.intervention_type == InterventionType.AUTO_RETRY
        assert decision.max_retries == 2


class TestAuthFailure:
    """Auth failure: <1000 → AUTO_RETRY(max=2), ≥1000 → CUSTOMER_NOTIFICATION."""

    def test_low_amount_auto_retry(self):
        decision = PolicyService.decide_intervention(
            _tx(Decimal("999.99")),
            _cls(FailureReasonType.AUTH_FAILURE, 0.8),
        )
        assert decision.intervention_type == InterventionType.AUTO_RETRY
        assert decision.max_retries == 2

    def test_at_boundary_notification(self):
        """amount=1000.00 (not < 1000) → CUSTOMER_NOTIFICATION."""
        decision = PolicyService.decide_intervention(
            _tx(Decimal("1000.00")),
            _cls(FailureReasonType.AUTH_FAILURE, 0.8),
        )
        assert decision.intervention_type == InterventionType.CUSTOMER_NOTIFICATION


class TestUnknownReason:
    """Unknown failure reason → always MANUAL_ESCALATION."""

    @pytest.mark.parametrize("amount", ["100.00", "5000.00", "9999.99"])
    def test_always_escalates(self, amount):
        decision = PolicyService.decide_intervention(
            _tx(Decimal(amount)),
            _cls(FailureReasonType.UNKNOWN, 0.9),
        )
        assert decision.intervention_type == InterventionType.MANUAL_ESCALATION


# ── Determinism ─────────────────────────────────────────────────────


class TestDeterminism:
    """Policy engine MUST be deterministic — same inputs → same output, every time."""

    def test_100_identical_calls(self):
        """Call decide_intervention 100 times with identical inputs → identical results."""
        tx = _tx(Decimal("500.00"))
        cls = _cls(FailureReasonType.BANK_ERROR, 0.9)

        first = PolicyService.decide_intervention(tx, cls)

        for i in range(100):
            result = PolicyService.decide_intervention(tx, cls)
            assert result.intervention_type == first.intervention_type, f"Mismatch at iteration {i}"
            assert result.max_retries == first.max_retries, f"Mismatch at iteration {i}"
            assert result.reason == first.reason, f"Mismatch at iteration {i}"
            assert result.requires_human_approval == first.requires_human_approval
