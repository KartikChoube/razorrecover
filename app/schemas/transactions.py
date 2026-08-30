"""Transaction schemas."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import PaymentMethod, TransactionStatus

class TransactionBase(BaseModel):
    """Base schema for transactions."""
    amount: Decimal = Field(gt=0)
    currency: str = Field(default="INR", max_length=3)
    payment_method: PaymentMethod
    failure_reason: str | None = None

class TransactionCreate(TransactionBase):
    """Schema for creating a transaction."""
    merchant_id: UUID
    customer_id: UUID
    status: TransactionStatus

class TransactionResponse(TransactionBase):
    """Schema for a transaction response."""
    model_config = ConfigDict(from_attributes=True)

    transaction_id: UUID
    merchant_id: UUID
    customer_id: UUID
    status: TransactionStatus
    created_at: datetime
    updated_at: datetime

class TransactionSummary(BaseModel):
    """Schema for transaction summary."""
    total_count: int
    success_count: int
    failed_count: int
    pending_count: int
    breakdown_by_method: dict[str, int]

from .failure_classifications import ClassificationResponse
from .interventions import InterventionResponse

class TransactionDetailResponse(TransactionResponse):
    """Transaction with nested classification and intervention details."""
    classifications: list[ClassificationResponse] = Field(default_factory=list)
    interventions: list[InterventionResponse] = Field(default_factory=list)

class SimulateFailureRequest(BaseModel):
    """Request body for simulating a transaction failure."""
    failure_reason: str = Field(min_length=1, max_length=255)

class PaginatedTransactionsResponse(BaseModel):
    """Paginated response wrapper."""
    transactions: list[TransactionResponse]
    total: int
    limit: int
    offset: int
