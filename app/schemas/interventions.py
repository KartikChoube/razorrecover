"""Intervention schemas."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.enums import InterventionStatus, InterventionType


class InterventionBase(BaseModel):
    """Base schema for an intervention."""
    intervention_type: InterventionType
    policy_decision_reason: str | None = None


class InterventionCreate(InterventionBase):
    """Schema for creating an intervention."""
    transaction_id: UUID
    max_retries: int | None = None


class InterventionResponse(InterventionBase):
    """Schema for an intervention response."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    transaction_id: UUID
    retry_count: int
    max_retries: int | None
    status: InterventionStatus
    created_at: datetime
    executed_at: datetime | None


class InterventionUpdate(BaseModel):
    """Schema for updating an intervention."""
    status: InterventionStatus | None = None
    retry_count: int | None = None
    executed_at: datetime | None = None


class BatchInterventionResponse(BaseModel):
    """Response from batch intervention processing."""
    processed: int
    auto_retry: int
    customer_notification: int  
    manual_escalation: int
    no_action: int
    errors: int


class RetryQueueStatus(BaseModel):
    """Status of the Redis retry queue."""
    queue_size: int
    pending_jobs: list[dict]


class ProcessDueRetriesResponse(BaseModel):
    """Response from processing due retries."""
    processed: int
    succeeded: int
    failed: int
    escalated: int


class PolicySummary(BaseModel):
    """Summary of the policy engine rules."""
    rules: dict
