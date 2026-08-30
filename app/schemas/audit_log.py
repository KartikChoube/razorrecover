"""Audit log schemas."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.enums import ActorType

class AuditLogBase(BaseModel):
    """Base schema for an audit log."""
    event_type: str
    event_details: dict | None = None
    actor: ActorType = ActorType.SYSTEM

class AuditLogCreate(AuditLogBase):
    """Schema for creating an audit log."""
    transaction_id: UUID | None = None

class AuditLogResponse(AuditLogBase):
    """Schema for an audit log response."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    transaction_id: UUID | None
    timestamp: datetime
