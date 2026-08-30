"""
Database initialization script for RazorRecover.
Creates all database tables based on SQLAlchemy models.

SQL DDL equivalent:
-- Enum types
CREATE TYPE transaction_status AS ENUM ('success', 'failed', 'pending');
CREATE TYPE payment_method AS ENUM ('upi', 'card', 'netbanking', 'wallet');
CREATE TYPE failure_reason_type AS ENUM ('insufficient_funds', 'bank_error', 'expired_card', 'auth_failure', 'unknown');
CREATE TYPE intervention_type AS ENUM ('auto_retry', 'customer_notification', 'manual_escalation', 'no_action');
CREATE TYPE intervention_status AS ENUM ('pending', 'executed', 'succeeded', 'failed', 'escalated');
CREATE TYPE actor_type AS ENUM ('system', 'ai', 'human');

-- Transactions table
CREATE TABLE transactions (
    transaction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id UUID NOT NULL,
    customer_id UUID NOT NULL,
    amount NUMERIC(12, 2) NOT NULL,
    currency VARCHAR(3) NOT NULL DEFAULT 'INR',
    status transaction_status NOT NULL,
    payment_method payment_method NOT NULL,
    failure_reason VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Failure classifications table
CREATE TABLE failure_classifications (
    id SERIAL PRIMARY KEY,
    transaction_id UUID NOT NULL REFERENCES transactions(transaction_id) ON DELETE CASCADE,
    predicted_reason failure_reason_type NOT NULL,
    confidence_score FLOAT NOT NULL CHECK (confidence_score >= 0 AND confidence_score <= 1),
    raw_llm_response JSONB,
    classified_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Interventions table
CREATE TABLE interventions (
    id SERIAL PRIMARY KEY,
    transaction_id UUID NOT NULL REFERENCES transactions(transaction_id) ON DELETE CASCADE,
    intervention_type intervention_type NOT NULL,
    policy_decision_reason TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER,
    status intervention_status NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    executed_at TIMESTAMPTZ
);

-- Audit log table
CREATE TABLE audit_log (
    id SERIAL PRIMARY KEY,
    transaction_id UUID REFERENCES transactions(transaction_id) ON DELETE CASCADE,
    event_type VARCHAR(100) NOT NULL,
    event_details JSONB,
    actor actor_type NOT NULL DEFAULT 'system',
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Recovery metrics table
CREATE TABLE recovery_metrics (
    id SERIAL PRIMARY KEY,
    batch_id UUID NOT NULL DEFAULT gen_random_uuid(),
    total_transactions INTEGER NOT NULL,
    total_failed INTEGER NOT NULL,
    revenue_at_risk NUMERIC(14, 2) NOT NULL,
    revenue_recovered NUMERIC(14, 2) NOT NULL DEFAULT 0,
    recovery_rate FLOAT NOT NULL DEFAULT 0,
    breakdown_by_reason JSONB,
    breakdown_by_intervention JSONB,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_transactions_status ON transactions(status);
CREATE INDEX idx_transactions_created_at ON transactions(created_at);
CREATE INDEX idx_fc_transaction_id ON failure_classifications(transaction_id);
CREATE INDEX idx_interventions_transaction_id ON interventions(transaction_id);
CREATE INDEX idx_interventions_status ON interventions(status);
CREATE INDEX idx_audit_log_transaction_id ON audit_log(transaction_id);
CREATE INDEX idx_audit_log_timestamp ON audit_log(timestamp);
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Add project root to sys.path to find the app package
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy.exc import SQLAlchemyError
from app.database import async_engine, Base
# Import all models to register them with Base.metadata
import app.models

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    """Initialize the database by creating all tables."""
    logger.info("🔧 Creating tables...")
    try:
        async with async_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            
        tables = Base.metadata.tables.keys()
        logger.info(f"✅ Done! Created tables: {', '.join(tables)}")
    except SQLAlchemyError as e:
        logger.error(f"❌ Failed to create tables: {e}")
    finally:
        await async_engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
