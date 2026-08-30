"""Failure classification schemas."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import FailureReasonType

class ClassificationBase(BaseModel):
    """Base schema for failure classification."""
    predicted_reason: FailureReasonType
    confidence_score: float = Field(ge=0, le=1)

class ClassificationCreate(ClassificationBase):
    """Schema for creating a classification."""
    transaction_id: UUID
    raw_llm_response: dict | None = None

class ClassificationResponse(ClassificationBase):
    """Schema for a classification response."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    transaction_id: UUID
    raw_llm_response: dict | None
    classified_at: datetime

class BatchClassificationResponse(BaseModel):
    """Response from batch classification run."""
    processed: int
    succeeded: int
    failed: int
    average_confidence: float

class AccuracyReport(BaseModel):
    """Classification accuracy report comparing predictions to ground truth."""
    overall_accuracy: float
    total_classified: int
    correct_predictions: int
    confusion_matrix: dict[str, dict[str, int]]
    per_reason_accuracy: dict[str, float]
    average_confidence: float
    high_confidence_accuracy: float
