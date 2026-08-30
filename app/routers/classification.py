"""Classification router endpoints."""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.audit_log import AuditLog
from app.models.enums import ActorType, FailureReasonType, TransactionStatus
from app.models.failure_classifications import FailureClassification
from app.models.transactions import Transaction
from app.schemas.failure_classifications import (
    AccuracyReport,
    BatchClassificationResponse,
    ClassificationResponse,
)
from app.services.classifier_service import ClassifierService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/classifications", tags=["Classifications"])
classifier_service = ClassifierService()

@router.post("/run-batch", response_model=BatchClassificationResponse)
async def run_batch(
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session)
) -> BatchClassificationResponse:
    """Batch classify unclassified failed transactions."""
    
    # Fetch failed transactions that DO NOT have a classification yet
    stmt = (
        select(Transaction)
        .outerjoin(FailureClassification, Transaction.transaction_id == FailureClassification.transaction_id)
        .where(Transaction.status == TransactionStatus.FAILED)
        .where(FailureClassification.id.is_(None))
        .limit(limit)
    )
    
    result = await session.execute(stmt)
    unclassified_transactions = result.scalars().all()
    
    processed = 0
    succeeded = 0
    failed = 0
    total_confidence = 0.0
    
    for tx in unclassified_transactions:
        try:
            classification = await classifier_service.classify_and_store(tx.transaction_id, session)
            if classification:
                succeeded += 1
                total_confidence += classification.confidence_score
            else:
                failed += 1
            await session.commit() # Commit each to avoid losing progress if batch is killed
            
        except Exception as e:
            logger.error(f"Error classifying transaction {tx.transaction_id}: {e}")
            await session.rollback()
            failed += 1
            
            # Write audit log for error
            try:
                error_audit = AuditLog(
                    transaction_id=tx.transaction_id,
                    event_type="classification_error",
                    event_details={"error": str(e)},
                    actor=ActorType.SYSTEM
                )
                session.add(error_audit)
                await session.commit()
            except Exception as audit_e:
                logger.error(f"Failed to write error audit log: {audit_e}")
                await session.rollback()
                
        # Sleep to avoid rate limits
        await asyncio.sleep(0.5)
        processed += 1
        
    avg_confidence = total_confidence / succeeded if succeeded > 0 else 0.0
    
    return BatchClassificationResponse(
        processed=processed,
        succeeded=succeeded,
        failed=failed,
        average_confidence=avg_confidence
    )


@router.get("/accuracy-report", response_model=AccuracyReport)
async def accuracy_report(
    session: AsyncSession = Depends(get_session)
) -> AccuracyReport:
    """DEVELOPMENT/EVALUATION ONLY.
    
    Compares AI predictions against ground-truth failure_reason.
    """
    stmt = (
        select(Transaction, FailureClassification)
        .join(FailureClassification, Transaction.transaction_id == FailureClassification.transaction_id)
        .where(Transaction.status == TransactionStatus.FAILED)
    )
    
    result = await session.execute(stmt)
    records = result.all()
    
    total_classified = len(records)
    if total_classified == 0:
        return AccuracyReport(
            overall_accuracy=0.0,
            total_classified=0,
            correct_predictions=0,
            confusion_matrix={},
            per_reason_accuracy={},
            average_confidence=0.0,
            high_confidence_accuracy=0.0
        )
        
    correct_predictions = 0
    total_confidence = 0.0
    confusion_matrix: dict[str, dict[str, int]] = {}
    reason_counts: dict[str, int] = {}
    reason_correct: dict[str, int] = {}
    
    high_conf_total = 0
    high_conf_correct = 0
    
    for tx, classification in records:
        # ground truth
        actual = tx.failure_reason or "unknown"
        predicted = classification.predicted_reason.value if isinstance(classification.predicted_reason, FailureReasonType) else str(classification.predicted_reason)
        
        # update matrix
        if actual not in confusion_matrix:
            confusion_matrix[actual] = {}
        confusion_matrix[actual][predicted] = confusion_matrix[actual].get(predicted, 0) + 1
        
        reason_counts[actual] = reason_counts.get(actual, 0) + 1
        total_confidence += classification.confidence_score
        
        is_correct = (actual == predicted)
        
        if is_correct:
            correct_predictions += 1
            reason_correct[actual] = reason_correct.get(actual, 0) + 1
            
        if classification.confidence_score >= 0.8:
            high_conf_total += 1
            if is_correct:
                high_conf_correct += 1
                
    overall_accuracy = (correct_predictions / total_classified) * 100.0
    avg_confidence = total_confidence / total_classified
    
    per_reason_accuracy = {
        reason: (reason_correct.get(reason, 0) / count) * 100.0
        for reason, count in reason_counts.items()
    }
    
    high_conf_acc = (high_conf_correct / high_conf_total * 100.0) if high_conf_total > 0 else 0.0
    
    return AccuracyReport(
        overall_accuracy=overall_accuracy,
        total_classified=total_classified,
        correct_predictions=correct_predictions,
        confusion_matrix=confusion_matrix,
        per_reason_accuracy=per_reason_accuracy,
        average_confidence=avg_confidence,
        high_confidence_accuracy=high_conf_acc
    )


@router.get("/{classification_id}", response_model=ClassificationResponse)
async def get_classification(
    classification_id: int,
    session: AsyncSession = Depends(get_session)
) -> ClassificationResponse:
    """Get a specific classification by ID."""
    stmt = select(FailureClassification).where(FailureClassification.id == classification_id)
    result = await session.execute(stmt)
    classification = result.scalar_one_or_none()
    
    if not classification:
        raise HTTPException(status_code=404, detail="Classification not found")
        
    return classification
