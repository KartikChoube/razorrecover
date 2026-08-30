import uuid
import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

async def test_list_transactions_pagination(client: AsyncClient, sample_transactions):
    response = await client.get("/api/v1/transactions/?limit=2")
    assert response.status_code == 200
    data = response.json()
    assert "transactions" in data
    assert len(data["transactions"]) == 2
    assert data["limit"] == 2

async def test_list_transactions_filter_status(client: AsyncClient, sample_transactions):
    response = await client.get("/api/v1/transactions/?status=failed")
    assert response.status_code == 200
    data = response.json()
    for txn in data["transactions"]:
        assert txn["status"] == "failed"
    assert len(data["transactions"]) == 3

async def test_get_transaction_by_id(client: AsyncClient, sample_transactions):
    txn_id = sample_transactions[0].transaction_id
    response = await client.get(f"/api/v1/transactions/{txn_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["transaction_id"] == str(txn_id)

async def test_get_transaction_not_found(client: AsyncClient):
    random_uuid = uuid.uuid4()
    response = await client.get(f"/api/v1/transactions/{random_uuid}")
    assert response.status_code == 404

async def test_get_stats(client: AsyncClient, sample_transactions):
    response = await client.get("/api/v1/transactions/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["total_count"] == 5
    assert data["failed_count"] == 3
    assert data["success_count"] == 2

async def test_simulate_failure(client: AsyncClient, sample_transactions):
    # Find a success transaction (index 3 in sample_transactions is success)
    txn = sample_transactions[3]
    assert txn.status.value == "success"
    
    response = await client.post(
        f"/api/v1/transactions/{txn.transaction_id}/simulate-failure",
        json={"failure_reason": "simulated_error"}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "failed"
    assert data["failure_reason"] == "simulated_error"

async def test_failed_unclassified(client: AsyncClient, sample_transactions):
    response = await client.get("/api/v1/transactions/failed-unclassified")
    assert response.status_code == 200
    data = response.json()
    # All 3 failed transactions have no classifications in the fixture
    assert len(data) == 3
    for txn in data:
        assert txn["status"] == "failed"
