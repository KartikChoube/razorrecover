from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, Enum as SQLAlchemyEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.enums import FailureReasonType

if TYPE_CHECKING:
    from app.models.transactions import Transaction


class FailureClassification(Base):
    """SQLAlchemy model for failure classifications."""
    __tablename__ = "failure_classifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.transaction_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    predicted_reason: Mapped[FailureReasonType] = mapped_column(
        SQLAlchemyEnum(FailureReasonType, name="failure_reason_type", create_constraint=True, native_enum=True),
        nullable=False,
    )
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    raw_llm_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    classified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    transaction: Mapped[Transaction] = relationship(back_populates="classifications")

    __table_args__ = (
        CheckConstraint("confidence_score >= 0 AND confidence_score <= 1", name="chk_confidence_score"),
    )

    def __repr__(self) -> str:
        return f"<FailureClassification(id={self.id}, transaction_id={self.transaction_id}, reason={self.predicted_reason})>"
