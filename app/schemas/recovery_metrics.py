"""Recovery metrics schemas."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

class RecoveryMetricsBase(BaseModel):
    """Base schema for recovery metrics."""
    total_transactions: int = 0
    total_failed: int = 0
    revenue_at_risk: Decimal = Field(default=Decimal("0.0"))
    revenue_recovered: Decimal = Field(default=Decimal("0.0"))
    recovery_rate: float = Field(default=0.0, ge=0.0, le=100.0)

class RecoveryMetricsCreate(RecoveryMetricsBase):
    """Schema for creating recovery metrics."""
    breakdown_by_reason: dict | None = None
    breakdown_by_intervention: dict | None = None

class RecoveryMetricsResponse(RecoveryMetricsBase):
    """Schema for a recovery metrics response."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    batch_id: UUID
    breakdown_by_reason: dict | None
    breakdown_by_intervention: dict | None
    computed_at: datetime
