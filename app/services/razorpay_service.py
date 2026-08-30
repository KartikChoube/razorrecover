"""
Razorpay Test-Mode API integration for payment recovery.

Retry/Backoff Strategy (plain English):
    When a Razorpay API call fails, we retry with exponential backoff:
    - Attempt 1: immediate
    - Attempt 2: wait 2 seconds
    - Attempt 3: wait 4 seconds  
    - After 3 failed attempts: mark intervention as 'failed' and
      automatically create a manual escalation entry.
    
    This ensures transient API issues are handled gracefully while
    preventing infinite retry loops that could overload the payment
    gateway or duplicate charges.

    ⚠️  TEST MODE ONLY — all operations use rzp_test_ keys.
    No real money is processed by this service.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict

import razorpay
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.audit_log import AuditLog
from app.models.enums import ActorType, InterventionStatus, InterventionType, TransactionStatus
from app.models.interventions import Intervention
from app.models.transactions import Transaction

logger = logging.getLogger(__name__)


class RazorpayService:
    """Razorpay service for handling test-mode payments and retries."""
    
    MAX_ATTEMPTS = 3
    BASE_DELAY_SECONDS = 2  # Doubles each retry: 2s, 4s
    
    def __init__(self):
        self._client = razorpay.Client(
            auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
        )

    async def execute_retry(
        self, transaction: Transaction, intervention: Intervention, session: AsyncSession
    ) -> Dict[str, Any]:
        """
        Execute an automatic retry using the Razorpay API with exponential backoff.
        
        Args:
            transaction: The failed transaction.
            intervention: The intervention record to track the retry.
            session: SQLAlchemy async session.
            
        Returns:
            Dictionary with status, order_id, attempts, and error details if any.
        """
        amount_paise = int(transaction.amount * 100)
        receipt_id = f"retry_{transaction.transaction_id}"
        
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                # Wrap synchronous Razorpay SDK call
                order = await asyncio.to_thread(
                    self._client.order.create,
                    {"amount": amount_paise, "currency": "INR", "receipt": receipt_id}
                )
                
                # Success path
                intervention.status = InterventionStatus.SUCCEEDED
                intervention.retry_count += 1
                intervention.executed_at = datetime.now(timezone.utc)
                
                transaction.status = TransactionStatus.SUCCESS
                
                audit = AuditLog(
                    transaction_id=transaction.transaction_id,
                    event_type="retry_succeeded",
                    actor=ActorType.SYSTEM,
                    event_details={
                        "attempt": attempt,
                        "order_id": order.get("id"),
                        "amount_paise": amount_paise
                    }
                )
                session.add(audit)
                await session.flush()
                
                return {
                    "status": "succeeded",
                    "order_id": order.get("id"),
                    "attempts": attempt
                }
                
            except (
                razorpay.errors.BadRequestError,
                razorpay.errors.ServerError,
                razorpay.errors.GatewayError,
                Exception
            ) as e:
                error_msg = str(e)
                logger.error(f"Razorpay retry attempt {attempt} failed: {error_msg}")
                
                if attempt < self.MAX_ATTEMPTS:
                    delay = self.BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                    
                    audit = AuditLog(
                        transaction_id=transaction.transaction_id,
                        event_type="retry_attempt_failed",
                        actor=ActorType.SYSTEM,
                        event_details={
                            "attempt": attempt,
                            "error": error_msg,
                            "next_retry_in": delay
                        }
                    )
                    session.add(audit)
                    await session.flush()
                    
                    await asyncio.sleep(delay)
                else:
                    # All attempts exhausted
                    intervention.status = InterventionStatus.FAILED
                    intervention.executed_at = datetime.now(timezone.utc)
                    
                    escalation_reason = (
                        f"Auto-retry exhausted after {self.MAX_ATTEMPTS} attempts. "
                        f"Last error: {error_msg}. Escalating to manual review."
                    )
                    
                    new_escalation = Intervention(
                        transaction_id=transaction.transaction_id,
                        intervention_type=InterventionType.MANUAL_ESCALATION,
                        policy_decision_reason=escalation_reason
                    )
                    session.add(new_escalation)
                    
                    audit = AuditLog(
                        transaction_id=transaction.transaction_id,
                        event_type="retry_exhausted_escalated",
                        actor=ActorType.SYSTEM,
                        event_details={
                            "attempts": attempt,
                            "error": error_msg
                        }
                    )
                    session.add(audit)
                    await session.flush()
                    
                    return {
                        "status": "failed",
                        "attempts": attempt,
                        "escalated": True,
                        "error": error_msg
                    }

        return {"status": "failed", "error": "Unknown error occurred during retry attempts"}

    async def create_payment_link(self, transaction: Transaction, message: str) -> Dict[str, Any]:
        """
        Create a Razorpay payment link for manual customer payment.
        
        Args:
            transaction: The failed transaction.
            message: Description to include in the payment link.
            
        Returns:
            Dictionary with payment link details or error information.
        """
        amount_paise = int(transaction.amount * 100)
        
        try:
            link = await asyncio.to_thread(
                self._client.payment_link.create,
                {
                    "amount": amount_paise,
                    "currency": "INR",
                    "description": message,
                    "customer": {},  # Would typically include customer details if available
                    "notify": {"email": True, "sms": True}
                }
            )
            return link
        except Exception as e:
            logger.error(f"Failed to create payment link: {str(e)}")
            return {"error": str(e)}
