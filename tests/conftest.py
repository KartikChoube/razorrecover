import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from app.database import Base, get_session
from app.main import app
from app.config import settings
from app.models import Transaction, TransactionStatus, PaymentMethod

# Use a separate test database or the same one with transaction rollback
TEST_DATABASE_URL = settings.DATABASE_URL  # In real prod, use a test DB

@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest_asyncio.fixture(scope="session")
async def test_engine():
    """Create a test engine."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()

@pytest_asyncio.fixture
async def db_session(test_engine):
    """Create a test session with transaction rollback."""
    async with test_engine.connect() as conn:
        transaction = await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        yield session
        await transaction.rollback()
        await session.close()

@pytest_asyncio.fixture
async def client(db_session):
    """Create an async test client with overridden DB session."""
    async def override_get_session():
        yield db_session
    app.dependency_overrides[get_session] = override_get_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()

@pytest_asyncio.fixture
async def sample_transactions(db_session):
    """Insert sample transactions for testing."""
    txns = []
    for i in range(5):
        t = Transaction(
            transaction_id=uuid.uuid4(),
            merchant_id=uuid.uuid4(),
            customer_id=uuid.uuid4(),
            amount=Decimal(f"{(i + 1) * 100}.00"),
            currency="INR",
            status=TransactionStatus.FAILED if i < 3 else TransactionStatus.SUCCESS,
            payment_method=PaymentMethod.UPI if i % 2 == 0 else PaymentMethod.CARD,
            failure_reason="insufficient_funds" if i < 3 else None,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        txns.append(t)
    db_session.add_all(txns)
    await db_session.flush()
    return txns
