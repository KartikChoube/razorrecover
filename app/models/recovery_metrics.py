from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Float, Integer, Numeric
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class RecoveryMetrics(Base):
    """SQLAlchemy model for recovery metrics."""
    __tablename__ = "recovery_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), default=uuid.uuid4, server_default=func.gen_random_uuid(), nullable=False
    )
    total_transactions: Mapped[int] = mapped_column(Integer, nullable=False)
    total_failed: Mapped[int] = mapped_column(Integer, nullable=False)
    revenue_at_risk: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    revenue_recovered: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), default=0, server_default="0", nullable=False
    )
    recovery_rate: Mapped[float] = mapped_column(Float, default=0.0, server_default="0", nullable=False)
    breakdown_by_reason: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    breakdown_by_intervention: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<RecoveryMetrics(batch_id={self.batch_id}, recovery_rate={self.recovery_rate})>"
