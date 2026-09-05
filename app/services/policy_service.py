"""
Deterministic Policy Engine for payment recovery decisions.

╔══════════════════════════════════════════════════════════════════════╗
║  CRITICAL DESIGN PRINCIPLE: This module contains ZERO LLM calls.   ║
║                                                                     ║
║  All decisions here are rule-based, deterministic, and auditable.   ║
║  The AI classifier (classifier_service.py) determines WHY a        ║
║  payment failed. This engine decides WHAT TO DO about it.           ║
║                                                                     ║
║  This separation exists because money-related actions must be       ║
║  explainable, bounded, and gated — never delegated to an LLM.      ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.enums import ActorType, FailureReasonType, InterventionType
from app.models.failure_classifications import FailureClassification
from app.models.interventions import Intervention
from app.models.transactions import Transaction

logger = logging.getLogger(__name__)

# ── Amount Thresholds (INR) ──────────────────────────────────────
LOW_AMOUNT_THRESHOLD = Decimal("1000.00")      # Below: low-risk auto-actions
MID_AMOUNT_THRESHOLD = Decimal("10000.00")     # Above: requires human review
CONFIDENCE_THRESHOLD = 0.5                      # Below: escalate regardless

# ── Retry Limits ─────────────────────────────────────────────────
MAX_RETRIES_LOW = 3       # Low-amount retries
MAX_RETRIES_MID = 2       # Mid-amount retries  
RETRY_DELAY_SECONDS = 1800  # 30 minutes between retries


@dataclass
class PolicyDecision:
    """Represents a deterministically decided policy action."""
    intervention_type: InterventionType
    reason: str
    max_retries: Optional[int] = None
    retry_delay_seconds: Optional[int] = None
    requires_human_approval: bool = False


class PolicyService:
    """Service to evaluate and apply recovery policies."""

    @classmethod
    def decide_intervention(
        cls, transaction: Transaction, classification: FailureClassification
    ) -> PolicyDecision:
        """
        Determine the appropriate intervention based on transaction details and failure classification.
        
        Args:
            transaction: The failed transaction.
            classification: The AI-determined reason for failure.
            
        Returns:
            PolicyDecision detailing what action to take and why.
        """
        amount = Decimal(str(transaction.amount))
        
        # GATE 1: Low confidence
        if classification.confidence_score < CONFIDENCE_THRESHOLD:
            return PolicyDecision(
                intervention_type=InterventionType.MANUAL_ESCALATION,
                reason=f"AI confidence too low for automated action ({classification.confidence_score}). Requires human review.",
                requires_human_approval=True
            )
            
        # GATE 2: High amount
        if amount > MID_AMOUNT_THRESHOLD:
            return PolicyDecision(
                intervention_type=InterventionType.MANUAL_ESCALATION,
                reason=f"Transaction amount ₹{amount} exceeds automated threshold of ₹10,000. Requires human approval.",
                requires_human_approval=True
            )
            
        reason_type = classification.predicted_reason
        
        # Rule: Expired Card
        if reason_type == FailureReasonType.EXPIRED_CARD:
            return PolicyDecision(
                intervention_type=InterventionType.CUSTOMER_NOTIFICATION,
                reason="Card expired — customer must update payment method. No retry possible.",
                max_retries=None,
                requires_human_approval=False
            )
            
        # Rule: Insufficient Funds
        if reason_type == FailureReasonType.INSUFFICIENT_FUNDS:
            if amount < LOW_AMOUNT_THRESHOLD:
                return PolicyDecision(
                    intervention_type=InterventionType.CUSTOMER_NOTIFICATION,
                    reason=f"Insufficient funds for ₹{amount}. Low-value transaction — notifying customer to retry with adequate balance."
                )
            else:  # amount 1000-10000
                return PolicyDecision(
                    intervention_type=InterventionType.CUSTOMER_NOTIFICATION,
                    reason=f"Insufficient funds for ₹{amount}. Customer notification required."
                )
                
        # Rule: Bank Error
        if reason_type == FailureReasonType.BANK_ERROR:
            if amount < LOW_AMOUNT_THRESHOLD:
                return PolicyDecision(
                    intervention_type=InterventionType.AUTO_RETRY,
                    reason=f"Bank error on low-value transaction (₹{amount}). Auto-retry safe — transient bank issues typically resolve.",
                    max_retries=MAX_RETRIES_LOW,
                    retry_delay_seconds=RETRY_DELAY_SECONDS
                )
            else:  # amount 1000-10000
                return PolicyDecision(
                    intervention_type=InterventionType.AUTO_RETRY,
                    reason=f"Bank error on mid-value transaction (₹{amount}). Limited auto-retry with monitoring.",
                    max_retries=MAX_RETRIES_MID,
                    retry_delay_seconds=RETRY_DELAY_SECONDS
                )
                
        # Rule: Auth Failure
        if reason_type == FailureReasonType.AUTH_FAILURE:
            if amount < LOW_AMOUNT_THRESHOLD:
                return PolicyDecision(
                    intervention_type=InterventionType.AUTO_RETRY,
                    reason=f"Auth failure on low-value transaction (₹{amount}). May be transient 3DS/OTP issue — retrying.",
                    max_retries=MAX_RETRIES_MID,
                    retry_delay_seconds=RETRY_DELAY_SECONDS
                )
            else:  # amount 1000-10000
                return PolicyDecision(
                    intervention_type=InterventionType.CUSTOMER_NOTIFICATION,
                    reason=f"Auth failure for ₹{amount}. Customer must re-authenticate — sending notification."
                )
                
        # Rule: Unknown
        if reason_type == FailureReasonType.UNKNOWN:
            return PolicyDecision(
                intervention_type=InterventionType.MANUAL_ESCALATION,
                reason="Unclassifiable failure reason. Cannot safely automate — escalating to human review.",
                requires_human_approval=True
            )
            
        # Default Fallback
        return PolicyDecision(
            intervention_type=InterventionType.NO_ACTION,
            reason="No matching policy rule. Defaulting to no action."
        )

    @classmethod
    async def decide_and_store(cls, transaction_id: UUID, session: AsyncSession) -> Optional[Intervention]:
        """
        Evaluate policy and persist the decision as an Intervention and AuditLog.
        
        Args:
            transaction_id: The UUID of the transaction.
            session: SQLAlchemy async session.
            
        Returns:
            The created Intervention record, or None if skipped.
        """
        # Fetch transaction
        tx_stmt = select(Transaction).where(Transaction.transaction_id == transaction_id)
        result = await session.execute(tx_stmt)
        transaction = result.scalar_one_or_none()
        
        if not transaction:
            logger.warning(f"Transaction {transaction_id} not found.")
            return None

        # Fetch latest classification
        cls_stmt = select(FailureClassification).where(
            FailureClassification.transaction_id == transaction_id
        ).order_by(FailureClassification.classified_at.desc()).limit(1)
        result = await session.execute(cls_stmt)
        classification = result.scalar_one_or_none()
        
        if not classification:
            logger.warning(f"No classification found for transaction {transaction_id}.")
            return None
            
        # Check if intervention already exists
        int_stmt = select(Intervention).where(Intervention.transaction_id == transaction_id)
        result = await session.execute(int_stmt)
        if result.scalar_one_or_none():
            logger.info(f"Intervention already exists for transaction {transaction_id}. Skipping.")
            return None

        # Decide policy
        decision = cls.decide_intervention(transaction, classification)
        logger.info(
            f"⚖️ Policy decision for tx {transaction_id} "
            f"(amount=₹{transaction.amount}, reason={classification.predicted_reason}, "
            f"conf={classification.confidence_score:.2f}) -> {decision.intervention_type.value}: {decision.reason}"
        )
        
        # Create Intervention
        intervention = Intervention(
            transaction_id=transaction_id,
            intervention_type=decision.intervention_type,
            policy_decision_reason=decision.reason,
            max_retries=decision.max_retries
        )
        session.add(intervention)
        
        # Prepare audit details
        audit_details = {
            "amount": str(transaction.amount),
            "confidence_score": classification.confidence_score,
            "failure_reason": classification.predicted_reason.value if hasattr(classification.predicted_reason, 'value') else str(classification.predicted_reason),
            "decision": {
                "intervention_type": decision.intervention_type.value if hasattr(decision.intervention_type, 'value') else str(decision.intervention_type),
                "reason": decision.reason,
                "requires_human_approval": decision.requires_human_approval
            }
        }
        
        if decision.intervention_type == InterventionType.AUTO_RETRY:
            audit_details["decision"]["retry_delay_seconds"] = decision.retry_delay_seconds
            
        # Create AuditLog
        audit_log = AuditLog(
            transaction_id=transaction_id,
            event_type="policy_decision",
            actor=ActorType.SYSTEM,
            event_details=audit_details
        )
        session.add(audit_log)
        
        await session.flush()
        return intervention

    @classmethod
    def get_policy_summary(cls) -> Dict[str, Any]:
        """
        Return the full rule table as a dictionary for documentation or dashboard purposes.
        """
        return {
            "thresholds": {
                "low_amount": float(LOW_AMOUNT_THRESHOLD),
                "mid_amount": float(MID_AMOUNT_THRESHOLD),
                "confidence": CONFIDENCE_THRESHOLD
            },
            "retry_limits": {
                "max_retries_low": MAX_RETRIES_LOW,
                "max_retries_mid": MAX_RETRIES_MID,
                "retry_delay_seconds": RETRY_DELAY_SECONDS
            },
            "rules": [
                {"condition": "confidence < 0.5", "action": "MANUAL_ESCALATION"},
                {"condition": "amount > 10000", "action": "MANUAL_ESCALATION"},
                {"condition": "reason == EXPIRED_CARD", "action": "CUSTOMER_NOTIFICATION"},
                {
                    "condition": "reason == INSUFFICIENT_FUNDS",
                    "sub_rules": [
                        {"condition": "amount < 1000", "action": "CUSTOMER_NOTIFICATION"},
                        {"condition": "amount 1000-10000", "action": "CUSTOMER_NOTIFICATION"}
                    ]
                },
                {
                    "condition": "reason == BANK_ERROR",
                    "sub_rules": [
                        {"condition": "amount < 1000", "action": "AUTO_RETRY", "max_retries": 3},
                        {"condition": "amount 1000-10000", "action": "AUTO_RETRY", "max_retries": 2}
                    ]
                },
                {
                    "condition": "reason == AUTH_FAILURE",
                    "sub_rules": [
                        {"condition": "amount < 1000", "action": "AUTO_RETRY", "max_retries": 2},
                        {"condition": "amount 1000-10000", "action": "CUSTOMER_NOTIFICATION"}
                    ]
                },
                {"condition": "reason == UNKNOWN", "action": "MANUAL_ESCALATION"},
                {"condition": "default", "action": "NO_ACTION"}
            ]
        }
