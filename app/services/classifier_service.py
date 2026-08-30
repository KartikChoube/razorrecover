"""AI-powered payment failure classifier service."""
from __future__ import annotations

import json
import logging
import random
import uuid
from typing import Any

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.enums import ActorType, TransactionStatus
from app.models.failure_classifications import FailureClassification
from app.models.transactions import Transaction

logger = logging.getLogger(__name__)

# Simulated gateway error codes mapping.
# In production, these would come from actual payment gateway responses.
FAILURE_REASON_TO_ERROR_CODE: dict[str, list[str]] = {
    "insufficient_funds": ["E001", "E001A"],
    "bank_error": ["E002", "E002A"],
    "expired_card": ["E003", "E003A"],
    "auth_failure": ["E004", "E004A"],
    "unknown": ["E005"],
}

ERROR_CODE_DESCRIPTIONS: dict[str, str] = {
    "E001": "Transaction declined - insufficient balance in customer account",
    "E001A": "Payment failed - account balance below required amount",
    "E002": "Bank server returned error during processing",
    "E002A": "Issuing bank connectivity timeout",
    "E003": "Card validation failed - card has expired",
    "E003A": "Payment instrument past expiration date",
    "E004": "Authentication failed - 3DS/OTP verification unsuccessful",
    "E004A": "Customer failed to complete 2FA verification",
    "E005": "Unknown error occurred during payment processing",
}

def generate_simulated_error_code(failure_reason: str) -> tuple[str, str, bool]:
    """Map the ground-truth failure_reason to a realistic error code.
    
    Returns a tuple of (error_code, error_description, was_noisy).
    """
    valid_reasons = list(FAILURE_REASON_TO_ERROR_CODE.keys())
    if failure_reason not in valid_reasons:
        failure_reason = "unknown"
        
    was_noisy = False
    
    # 10% chance to return a noisy/incorrect error code
    if random.random() < 0.10:
        was_noisy = True
        other_reasons = [r for r in valid_reasons if r != failure_reason]
        failure_reason = random.choice(other_reasons)
        
    error_code = random.choice(FAILURE_REASON_TO_ERROR_CODE[failure_reason])
    error_description = ERROR_CODE_DESCRIPTIONS[error_code]
    
    return error_code, error_description, was_noisy


class ClassifierService:
    """AI-powered payment failure classifier using Anthropic Claude.
    
    Uses LLM to classify payment failure reasons from transaction metadata
    and simulated gateway error codes. The classifier NEVER sees the ground-truth
    failure_reason - it only receives the error code and transaction context.
    """
    
    VALID_REASONS = {"insufficient_funds", "bank_error", "expired_card", "auth_failure", "unknown"}
    
    def __init__(self) -> None:
        self._client: anthropic.AsyncAnthropic | None = None
    
    @property
    def client(self) -> anthropic.AsyncAnthropic:
        """Lazy load Anthropic client."""
        if self._client is None:
            from app.config import settings
            self._client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        return self._client
        
    async def classify_failure(self, transaction_data: dict[str, Any]) -> dict[str, Any]:
        """Classify a failure using LLM."""
        system_prompt = (
            "You are a senior payment failure analyst at a fintech company. Analyze the "
            "transaction data and gateway error code to determine why the payment failed.\n\n"
            "You MUST respond with ONLY valid JSON (no markdown, no code fences, no extra text). "
            "The JSON must have exactly these fields:\n"
            '- "predicted_reason": one of ["insufficient_funds", "bank_error", "expired_card", "auth_failure", "unknown"]\n'
            '- "confidence_score": float between 0.0 and 1.0\n'
            '- "reasoning": string, one sentence, maximum 20 words'
        )
        
        user_message = f"Transaction Data:\n{json.dumps(transaction_data, indent=2, default=str)}"
        
        fallback = {
            "predicted_reason": "unknown", 
            "confidence_score": 0.0, 
            "reasoning": "classification_failed",
            "raw_response": None
        }

        messages = [{"role": "user", "content": user_message}]
        
        for attempt in range(2):
            try:
                response = await self.client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=256,
                    system=system_prompt,
                    messages=messages,
                )
                
                response_text = response.content[0].text
                
                try:
                    parsed = json.loads(response_text)
                    
                    predicted_reason = parsed.get("predicted_reason")
                    confidence_score = parsed.get("confidence_score")
                    reasoning = parsed.get("reasoning", "")
                    
                    if predicted_reason not in self.VALID_REASONS:
                        raise ValueError(f"Invalid reason: {predicted_reason}")
                        
                    if not isinstance(confidence_score, (int, float)) or not (0.0 <= confidence_score <= 1.0):
                        raise ValueError(f"Invalid confidence: {confidence_score}")
                        
                    return {
                        "predicted_reason": predicted_reason,
                        "confidence_score": float(confidence_score),
                        "reasoning": str(reasoning),
                        "raw_response": response.model_dump()
                    }
                    
                except (json.JSONDecodeError, ValueError) as e:
                    logger.warning(f"JSON parsing/validation failed on attempt {attempt+1}: {e}")
                    if attempt == 0:
                        messages.append({"role": "assistant", "content": response_text})
                        messages.append({
                            "role": "user", 
                            "content": "Your previous response was not valid JSON. Respond with ONLY a JSON object, no other text."
                        })
                    else:
                        logger.error("Failed to get valid JSON from LLM after retry.")
                        return fallback
                        
            except (
                anthropic.APIError, 
                anthropic.APITimeoutError, 
                anthropic.RateLimitError, 
                anthropic.APIConnectionError
            ) as e:
                logger.error(f"Anthropic API error: {e}")
                return fallback
            except Exception as e:
                logger.error(f"Unexpected error in classification: {e}")
                return fallback

        return fallback

    async def classify_and_store(
        self, 
        transaction_id: uuid.UUID, 
        session: AsyncSession
    ) -> FailureClassification | None:
        """Classify a transaction and store the result."""
        # 1. Fetch transaction
        from sqlalchemy import select
        result = await session.execute(
            select(Transaction).where(Transaction.transaction_id == transaction_id)
        )
        transaction = result.scalar_one_or_none()
        
        if not transaction or transaction.status != TransactionStatus.FAILED:
            return None
            
        # 2. Simulate error code (don't send true failure_reason)
        gt_reason = transaction.failure_reason or "unknown"
        error_code, error_description, was_noisy = generate_simulated_error_code(gt_reason)
        
        # 3. Build data
        tx_data = {
            "amount": float(transaction.amount),
            "currency": transaction.currency,
            "payment_method": transaction.payment_method.value,
            "created_at": transaction.created_at.isoformat(),
            "simulated_error_code": error_code,
            "error_description": error_description
        }
        
        # 4. Classify
        classification_result = await self.classify_failure(tx_data)
        
        # 5. Store classification
        classification = FailureClassification(
            transaction_id=transaction.transaction_id,
            predicted_reason=classification_result["predicted_reason"],
            confidence_score=classification_result["confidence_score"],
            raw_llm_response=classification_result.get("raw_response")
        )
        session.add(classification)
        
        # 6. Audit log
        audit_details = {
            "predicted_reason": classification_result["predicted_reason"],
            "confidence_score": classification_result["confidence_score"],
            "reasoning": classification_result.get("reasoning"),
            "error_code_used": error_code,
            "was_noisy": was_noisy
        }
        
        audit_log = AuditLog(
            transaction_id=transaction.transaction_id,
            event_type="classification_completed",
            event_details=audit_details,
            actor=ActorType.AI
        )
        session.add(audit_log)
        
        await session.flush()
        return classification
