from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, Text, Enum as SQLAlchemyEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.enums import InterventionStatus, InterventionType

if TYPE_CHECKING:
    from app.models.transactions import Transaction


class Intervention(Base):
    """SQLAlchemy model for interventions."""
    __tablename__ = "interventions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.transaction_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    intervention_type: Mapped[InterventionType] = mapped_column(
        SQLAlchemyEnum(InterventionType, name="intervention_type", create_constraint=True, native_enum=True),
        nullable=False,
    )
    policy_decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    max_retries: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[InterventionStatus] = mapped_column(
        SQLAlchemyEnum(InterventionStatus, name="intervention_status", create_constraint=True, native_enum=True),
        default=InterventionStatus.PENDING,
        server_default="pending",
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    executed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    transaction: Mapped[Transaction] = relationship(back_populates="interventions")

    def __repr__(self) -> str:
        return f"<Intervention(id={self.id}, transaction_id={self.transaction_id}, status={self.status})>"
