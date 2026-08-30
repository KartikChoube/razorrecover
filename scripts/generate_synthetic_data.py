"""
Generates synthetic transaction data for the RazorRecover project.

NOTE: This script requires `numpy` and `faker`.
Install via: pip install numpy faker

Generates 8,000 transactions:
- 70% success, 30% failed
- Distributed payment methods and failure reasons
- Amounts follow a log-normal distribution
- Inserted into the database using batching
"""

from __future__ import annotations

import asyncio
import logging
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from decimal import Decimal

# Add project root to sys.path to find the app package
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

try:
    import numpy as np
except ImportError:
    print("❌ Error: numpy is required for this script.")
    print("Please install it using: pip install numpy")
    sys.exit(1)

try:
    from faker import Faker
except ImportError:
    print("❌ Error: faker is required for this script.")
    print("Please install it using: pip install faker")
    sys.exit(1)

from sqlalchemy import select, func
from sqlalchemy.exc import SQLAlchemyError
from app.database import AsyncSessionLocal
from app.models import Transaction
from app.models.enums import TransactionStatus, PaymentMethod, FailureReasonType

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# Constants
TOTAL_TRANSACTIONS = 8000
BATCH_SIZE = 500
MERCHANT_COUNT = 50
CUSTOMER_COUNT = 3000

# Probabilities
SUCCESS_RATE = 0.70
FAILURE_RATE = 0.30

# Failure breakdowns
FAILURE_BREAKDOWN = {
    FailureReasonType.INSUFFICIENT_FUNDS: 0.30,
    FailureReasonType.BANK_ERROR: 0.20,
    FailureReasonType.EXPIRED_CARD: 0.20,
    FailureReasonType.AUTH_FAILURE: 0.15,
    FailureReasonType.UNKNOWN: 0.15,
}

# Payment method breakdowns
PAYMENT_METHOD_BREAKDOWN = {
    PaymentMethod.UPI: 0.50,
    PaymentMethod.CARD: 0.30,
    PaymentMethod.NETBANKING: 0.15,
    PaymentMethod.WALLET: 0.05,
}

def generate_random_date(fake: Faker) -> datetime:
    """Generate a random date in the last 30 days, clustered 10am-10pm IST."""
    now = datetime.now(timezone.utc)
    start_date = now - timedelta(days=30)
    
    # Random date within last 30 days
    random_days = random.uniform(0, 30)
    base_date = start_date + timedelta(days=random_days)
    
    # 80% chance between 10am and 10pm IST
    if random.random() < 0.8:
        # Generate hour between 10 and 21 (inclusive)
        hour = random.randint(10, 21)
    else:
        # Generate hour outside 10-21
        hour = random.choice(list(range(0, 10)) + list(range(22, 24)))
        
    minute = random.randint(0, 59)
    second = random.randint(0, 59)
    
    # Set time for IST, converting back to UTC for database storage
    ist_time = base_date.replace(hour=hour, minute=minute, second=second)
    utc_time = ist_time - timedelta(hours=5, minutes=30)
    
    return utc_time

async def check_idempotency(session) -> bool:
    """Check if data already exists to avoid duplicate generation."""
    try:
        result = await session.execute(select(func.count(Transaction.transaction_id)))
        count = result.scalar() or 0
        if count >= TOTAL_TRANSACTIONS:
            logger.warning(f"⚠️ Database already has {count} transactions. Exiting.")
            return False
        elif count > 0:
            logger.warning(f"⚠️ Database has {count} transactions. Exiting to avoid partial state.")
            return False
        return True
    except SQLAlchemyError as e:
        logger.error(f"❌ Database error checking idempotency: {e}")
        return False

def print_summary(transactions: list[Transaction]):
    """Prints a formatted summary table of generated transactions."""
    total = len(transactions)
    success_count = sum(1 for t in transactions if t.status == TransactionStatus.SUCCESS)
    failed_count = sum(1 for t in transactions if t.status == TransactionStatus.FAILED)
    
    failed_txns = [t for t in transactions if t.status == TransactionStatus.FAILED]
    
    # Note: failure_reason might be enum or string depending on exact model mapping. 
    # Using enum values to match.
    insufficient = sum(1 for t in failed_txns if t.failure_reason in (FailureReasonType.INSUFFICIENT_FUNDS, FailureReasonType.INSUFFICIENT_FUNDS.value))
    bank_err = sum(1 for t in failed_txns if t.failure_reason in (FailureReasonType.BANK_ERROR, FailureReasonType.BANK_ERROR.value))
    expired = sum(1 for t in failed_txns if t.failure_reason in (FailureReasonType.EXPIRED_CARD, FailureReasonType.EXPIRED_CARD.value))
    auth_fail = sum(1 for t in failed_txns if t.failure_reason in (FailureReasonType.AUTH_FAILURE, FailureReasonType.AUTH_FAILURE.value))
    unknown = sum(1 for t in failed_txns if t.failure_reason in (FailureReasonType.UNKNOWN, FailureReasonType.UNKNOWN.value))
    
    upi = sum(1 for t in transactions if t.payment_method in (PaymentMethod.UPI, PaymentMethod.UPI.value))
    card = sum(1 for t in transactions if t.payment_method in (PaymentMethod.CARD, PaymentMethod.CARD.value))
    netbanking = sum(1 for t in transactions if t.payment_method in (PaymentMethod.NETBANKING, PaymentMethod.NETBANKING.value))
    wallet = sum(1 for t in transactions if t.payment_method in (PaymentMethod.WALLET, PaymentMethod.WALLET.value))
    
    amounts = [float(t.amount) for t in transactions]
    min_amt = min(amounts)
    max_amt = max(amounts)
    avg_amt = sum(amounts) / total
    revenue_at_risk = sum(float(t.amount) for t in failed_txns)
    
    print("\n╔══════════════════════════════════════════════════════════════╗")
    print("║              RazorRecover — Synthetic Data Summary           ║")
    print("╠══════════════════════════════════════════════════════════════╣")
    print(f"║ Total Transactions: {total:<40} ║")
    print("╠══════════════════════════════════════════════════════════════╣")
    print("║ By Status:                                                   ║")
    print(f"║   ✅ Success:  {success_count:<5} ({success_count/total*100:>4.1f}%)                                  ║")
    print(f"║   ❌ Failed:   {failed_count:<5} ({failed_count/total*100:>4.1f}%)                                  ║")
    print("╠══════════════════════════════════════════════════════════════╣")
    print("║ Failed Breakdown:                                            ║")
    print(f"║   💰 Insufficient Funds: {insufficient:<3} ({insufficient/failed_count*100:>4.1f}%)                          ║")
    print(f"║   🏦 Bank Error:         {bank_err:<3} ({bank_err/failed_count*100:>4.1f}%)                          ║")
    print(f"║   💳 Expired Card:       {expired:<3} ({expired/failed_count*100:>4.1f}%)                          ║")
    print(f"║   🔐 Auth Failure:       {auth_fail:<3} ({auth_fail/failed_count*100:>4.1f}%)                          ║")
    print(f"║   ❓ Unknown:            {unknown:<3} ({unknown/failed_count*100:>4.1f}%)                          ║")
    print("╠══════════════════════════════════════════════════════════════╣")
    print("║ By Payment Method:                                           ║")
    print(f"║   📱 UPI:        {upi:<5} ({upi/total*100:>4.1f}%)                                  ║")
    print(f"║   💳 Card:       {card:<5} ({card/total*100:>4.1f}%)                                  ║")
    print(f"║   🌐 Netbanking: {netbanking:<5} ({netbanking/total*100:>4.1f}%)                                  ║")
    print(f"║   👛 Wallet:     {wallet:<5} ({wallet/total*100:>4.1f}%)                                  ║")
    print("╠══════════════════════════════════════════════════════════════╣")
    print("║ Amount Statistics:                                           ║")
    print(f"║   Min: ₹{min_amt:,.2f}{' ' * max(0, 48 - len(f'₹{min_amt:,.2f}'))} ║")
    print(f"║   Max: ₹{max_amt:,.2f}{' ' * max(0, 48 - len(f'₹{max_amt:,.2f}'))} ║")
    print(f"║   Avg: ₹{avg_amt:,.2f}{' ' * max(0, 48 - len(f'₹{avg_amt:,.2f}'))} ║")
    print(f"║   Total Revenue at Risk: ₹{revenue_at_risk:,.2f}{' ' * max(0, 34 - len(f'₹{revenue_at_risk:,.2f}'))} ║")
    print("╚══════════════════════════════════════════════════════════════╝")


async def main() -> None:
    """Generate and insert synthetic transaction data."""
    # Seed random generators
    random.seed(42)
    np.random.seed(42)
    Faker.seed(42)
    fake = Faker()

    async with AsyncSessionLocal() as session:
        if not await check_idempotency(session):
            return

        logger.info("🔧 Starting synthetic data generation...")
        
        # Pre-generate UUID pools
        merchant_ids = [uuid.uuid4() for _ in range(MERCHANT_COUNT)]
        customer_ids = [uuid.uuid4() for _ in range(CUSTOMER_COUNT)]
        
        # Generate target counts
        num_success = int(TOTAL_TRANSACTIONS * SUCCESS_RATE)
        num_failed = int(TOTAL_TRANSACTIONS * FAILURE_RATE)
        
        # Generate exact failure breakdown
        failed_reasons = (
            [FailureReasonType.INSUFFICIENT_FUNDS.value] * int(num_failed * FAILURE_BREAKDOWN[FailureReasonType.INSUFFICIENT_FUNDS]) +
            [FailureReasonType.BANK_ERROR.value] * int(num_failed * FAILURE_BREAKDOWN[FailureReasonType.BANK_ERROR]) +
            [FailureReasonType.EXPIRED_CARD.value] * int(num_failed * FAILURE_BREAKDOWN[FailureReasonType.EXPIRED_CARD]) +
            [FailureReasonType.AUTH_FAILURE.value] * int(num_failed * FAILURE_BREAKDOWN[FailureReasonType.AUTH_FAILURE]) +
            [FailureReasonType.UNKNOWN.value] * int(num_failed * FAILURE_BREAKDOWN[FailureReasonType.UNKNOWN])
        )
        
        # Adjust for rounding errors
        while len(failed_reasons) < num_failed:
            failed_reasons.append(FailureReasonType.UNKNOWN.value)
        random.shuffle(failed_reasons)
        
        # Generate exact payment method breakdown
        payment_methods = (
            [PaymentMethod.UPI] * int(TOTAL_TRANSACTIONS * PAYMENT_METHOD_BREAKDOWN[PaymentMethod.UPI]) +
            [PaymentMethod.CARD] * int(TOTAL_TRANSACTIONS * PAYMENT_METHOD_BREAKDOWN[PaymentMethod.CARD]) +
            [PaymentMethod.NETBANKING] * int(TOTAL_TRANSACTIONS * PAYMENT_METHOD_BREAKDOWN[PaymentMethod.NETBANKING]) +
            [PaymentMethod.WALLET] * int(TOTAL_TRANSACTIONS * PAYMENT_METHOD_BREAKDOWN[PaymentMethod.WALLET])
        )
        
        while len(payment_methods) < TOTAL_TRANSACTIONS:
            payment_methods.append(PaymentMethod.UPI)
        random.shuffle(payment_methods)
        
        # Generate amounts (log-normal)
        amounts = np.random.lognormal(mean=6.5, sigma=1.0, size=TOTAL_TRANSACTIONS)
        amounts = np.clip(amounts, 100.0, 50000.0)
        
        transactions = []
        failure_idx = 0
        
        for i in range(TOTAL_TRANSACTIONS):
            is_success = i < num_success
            status = TransactionStatus.SUCCESS if is_success else TransactionStatus.FAILED
            failure_reason = None
            if not is_success:
                failure_reason = failed_reasons[failure_idx]
                failure_idx += 1
                
            amount = Decimal(str(round(amounts[i], 2)))
            created_at = generate_random_date(fake)
            
            txn = Transaction(
                transaction_id=uuid.uuid4(),
                merchant_id=random.choice(merchant_ids),
                customer_id=random.choice(customer_ids),
                amount=amount,
                currency="INR",
                status=status,
                payment_method=payment_methods[i],
                failure_reason=failure_reason,
                created_at=created_at,
                updated_at=created_at
            )
            transactions.append(txn)
        
        # Shuffle transactions so successes and failures are mixed
        random.shuffle(transactions)
        
        # Batch insert
        try:
            total_batches = (TOTAL_TRANSACTIONS + BATCH_SIZE - 1) // BATCH_SIZE
            for i in range(0, TOTAL_TRANSACTIONS, BATCH_SIZE):
                batch = transactions[i:i + BATCH_SIZE]
                session.add_all(batch)
                await session.commit()
                batch_num = i // BATCH_SIZE + 1
                logger.info(f"Generating transactions... [batch {batch_num}/{total_batches}]")
            
            print_summary(transactions)
            
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error(f"❌ Failed to insert synthetic data: {e}")

if __name__ == "__main__":
    asyncio.run(main())
