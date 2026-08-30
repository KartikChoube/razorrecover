from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Enum as SQLAlchemyEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.enums import ActorType

if TYPE_CHECKING:
    from app.models.transactions import Transaction


class AuditLog(Base):
    """SQLAlchemy model for audit logs."""
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("transactions.transaction_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    event_details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    actor: Mapped[ActorType] = mapped_column(
        SQLAlchemyEnum(ActorType, name="actor_type", create_constraint=True, native_enum=True),
        default=ActorType.SYSTEM,
        server_default="system",
        nullable=False,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    transaction: Mapped[Transaction | None] = relationship(back_populates="audit_logs")

    def __repr__(self) -> str:
        return f"<AuditLog(id={self.id}, event_type={self.event_type}, actor={self.actor})>"
