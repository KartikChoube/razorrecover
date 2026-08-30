import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_session
from app.config import settings
from app.models import (
    Transaction,
    FailureClassification,
    Intervention,
    AuditLog,
)
from app.models.enums import TransactionStatus, ActorType
from app.schemas import (
    TransactionResponse,
    TransactionSummary,
    TransactionDetailResponse,
    SimulateFailureRequest,
    PaginatedTransactionsResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transactions", tags=["Transactions"])

@router.get("/health", summary="Health Check")
async def get_health(session: AsyncSession = Depends(get_session)):
    """DB + Redis health check."""
    status_dict = {"database": "unhealthy", "redis": "unhealthy", "overall": "degraded"}
    
    # Check DB
    try:
        await session.execute(select(1))
        status_dict["database"] = "healthy"
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
    
    # Check Redis
    try:
        import redis.asyncio as redis
        redis_client = redis.from_url(settings.REDIS_URL)
        await redis_client.ping()
        status_dict["redis"] = "healthy"
        await redis_client.aclose()
    except Exception as e:
        logger.error(f"Redis health check failed: {e}")
    
    if status_dict["database"] == "healthy" and status_dict["redis"] == "healthy":
        status_dict["overall"] = "healthy"
        
    return status_dict

@router.get("/stats", response_model=TransactionSummary, summary="Transaction statistics")
async def get_transaction_stats(session: AsyncSession = Depends(get_session)):
    """Return transaction statistics."""
    try:
        # Total count
        total = await session.scalar(select(func.count()).select_from(Transaction))
        
        # Breakdown by status
        status_counts_result = await session.execute(
            select(Transaction.status, func.count()).group_by(Transaction.status)
        )
        status_counts = dict(status_counts_result.all())
        
        success = status_counts.get(TransactionStatus.SUCCESS, 0)
        failed = status_counts.get(TransactionStatus.FAILED, 0)
        pending = status_counts.get(TransactionStatus.PENDING, 0)
        
        # Breakdown by method
        method_counts_result = await session.execute(
            select(Transaction.payment_method, func.count()).group_by(Transaction.payment_method)
        )
        # Convert enum names/values properly for the dictionary
        method_counts = {k.value if hasattr(k, 'value') else str(k): v for k, v in method_counts_result.all()}
        
        return TransactionSummary(
            total_count=total or 0,
            success_count=success,
            failed_count=failed,
            pending_count=pending,
            breakdown_by_method=method_counts
        )
    except Exception as e:
        logger.exception("Failed to compute stats")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/failed-unclassified", response_model=list[TransactionResponse], summary="Failed transactions without classifications")
async def get_failed_unclassified(
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session)
):
    """SELECT failed transactions that don't have any entries in failure_classifications table."""
    try:
        stmt = (
            select(Transaction)
            .outerjoin(FailureClassification, Transaction.transaction_id == FailureClassification.transaction_id)
            .where(Transaction.status == TransactionStatus.FAILED)
            .where(FailureClassification.id.is_(None))
            .order_by(Transaction.created_at.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())
    except Exception as e:
        logger.exception("Failed to get unclassified transactions")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/", response_model=PaginatedTransactionsResponse, summary="List transactions")
async def list_transactions(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: TransactionStatus | None = None,
    merchant_id: UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    session: AsyncSession = Depends(get_session)
):
    """List transactions with pagination and filters."""
    try:
        stmt = select(Transaction)
        count_stmt = select(func.count()).select_from(Transaction)
        
        if status:
            stmt = stmt.where(Transaction.status == status)
            count_stmt = count_stmt.where(Transaction.status == status)
        if merchant_id:
            stmt = stmt.where(Transaction.merchant_id == merchant_id)
            count_stmt = count_stmt.where(Transaction.merchant_id == merchant_id)
        if date_from:
            stmt = stmt.where(Transaction.created_at >= date_from)
            count_stmt = count_stmt.where(Transaction.created_at >= date_from)
        if date_to:
            stmt = stmt.where(Transaction.created_at <= date_to)
            count_stmt = count_stmt.where(Transaction.created_at <= date_to)
            
        stmt = stmt.order_by(Transaction.created_at.desc()).offset(offset).limit(limit)
        
        total_count = await session.scalar(count_stmt)
        result = await session.execute(stmt)
        transactions = list(result.scalars().all())
        
        return PaginatedTransactionsResponse(
            transactions=transactions,
            total=total_count or 0,
            limit=limit,
            offset=offset
        )
    except Exception as e:
        logger.exception("Failed to list transactions")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/{transaction_id}", response_model=TransactionDetailResponse, summary="Get single transaction")
async def get_transaction(
    transaction_id: UUID,
    session: AsyncSession = Depends(get_session)
):
    """Single transaction with nested data."""
    try:
        stmt = (
            select(Transaction)
            .where(Transaction.transaction_id == transaction_id)
            .options(
                selectinload(Transaction.classifications),
                selectinload(Transaction.interventions)
            )
        )
        result = await session.execute(stmt)
        transaction = result.scalar_one_or_none()
        
        if not transaction:
            raise HTTPException(status_code=404, detail="Transaction not found")
            
        return transaction
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get transaction")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/{transaction_id}/simulate-failure", response_model=TransactionResponse, summary="Simulate failure")
async def simulate_failure(
    transaction_id: UUID,
    body: SimulateFailureRequest,
    session: AsyncSession = Depends(get_session)
):
    """Testing utility: Simulate a transaction failure."""
    try:
        stmt = select(Transaction).where(Transaction.transaction_id == transaction_id)
        result = await session.execute(stmt)
        transaction = result.scalar_one_or_none()
        
        if not transaction:
            raise HTTPException(status_code=404, detail="Transaction not found")
            
        prev_status = transaction.status
        
        transaction.status = TransactionStatus.FAILED
        transaction.failure_reason = body.failure_reason
        transaction.updated_at = func.now()
        
        audit_log = AuditLog(
            transaction_id=transaction.transaction_id,
            event_type="failure_simulated",
            actor=ActorType.HUMAN,
            event_details={
                "previous_status": prev_status.value if hasattr(prev_status, 'value') else str(prev_status),
                "new_failure_reason": body.failure_reason
            }
        )
        session.add(audit_log)
        await session.commit()
        await session.refresh(transaction)
        
        return transaction
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to simulate failure")
        await session.rollback()
        raise HTTPException(status_code=500, detail="Internal server error")
