"""
Generates synthetic transaction data for the RazorRecover project.

NOTE: This script requires `numpy` and `faker`.
Install via: pip install numpy faker

Generates synthetic transactions (default ~670 total with exactly ~200 failed):
- ~70% success (~470), ~30% failed (~200)
- Proportional failure reason distribution:
    - insufficient_funds: 30% (~60)
    - bank_error: 20% (~40)
    - expired_card: 20% (~40)
    - auth_failure: 15% (~30)
    - unknown: 15% (~30)
- Distributed payment methods and log-normal amounts
- Configurable via CLI arguments (--total, --failed, --clear)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

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

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.database import AsyncSessionLocal
from app.models import (
    AuditLog,
    FailureClassification,
    Intervention,
    RecoveryMetrics,
    Transaction,
)
from app.models.enums import FailureReasonType, PaymentMethod, TransactionStatus

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# Defaults
DEFAULT_TOTAL_TRANSACTIONS = 670
DEFAULT_FAILED_TRANSACTIONS = 200
BATCH_SIZE = 200
MERCHANT_COUNT = 50
CUSTOMER_COUNT = 500

# Failure breakdowns (proportions of failed transactions)
FAILURE_BREAKDOWN = {
    FailureReasonType.INSUFFICIENT_FUNDS: 0.30,  # 30% of 200 = 60
    FailureReasonType.BANK_ERROR: 0.20,          # 20% of 200 = 40
    FailureReasonType.EXPIRED_CARD: 0.20,        # 20% of 200 = 40
    FailureReasonType.AUTH_FAILURE: 0.15,        # 15% of 200 = 30
    FailureReasonType.UNKNOWN: 0.15,             # 15% of 200 = 30
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
        hour = random.randint(10, 21)
    else:
        hour = random.choice(list(range(0, 10)) + list(range(22, 24)))

    minute = random.randint(0, 59)
    second = random.randint(0, 59)

    # Set time for IST, converting back to UTC for database storage
    ist_time = base_date.replace(hour=hour, minute=minute, second=second)
    utc_time = ist_time - timedelta(hours=5, minutes=30)

    return utc_time


async def clear_existing_data(session) -> None:
    """Wipe existing transaction and recovery records before fresh generation."""
    logger.info("🗑️  Clearing existing data from database tables...")
    try:
        # PostgreSQL fast truncate with cascade
        await session.execute(
            text(
                "TRUNCATE TABLE transactions, failure_classifications, interventions, audit_log, recovery_metrics CASCADE;"
            )
        )
        await session.commit()
        logger.info("✅ Database tables truncated successfully.")
    except Exception as exc:
        await session.rollback()
        logger.info(f"ℹ️  TRUNCATE CASCADE failed ({exc}). Falling back to table delete statements...")
        # Fallback in reverse dependency order
        await session.execute(delete(RecoveryMetrics))
        await session.execute(delete(AuditLog))
        await session.execute(delete(Intervention))
        await session.execute(delete(FailureClassification))
        await session.execute(delete(Transaction))
        await session.commit()
        logger.info("✅ All records deleted successfully.")


async def check_idempotency(session, total_expected: int) -> bool:
    """Check if data already exists to avoid duplicate generation."""
    try:
        result = await session.execute(select(func.count(Transaction.transaction_id)))
        count = result.scalar() or 0
        if count >= total_expected:
            logger.warning(
                f"⚠️ Database already has {count} transactions (threshold: {total_expected}). Exiting.\n"
                f"   To wipe and regenerate fresh data, run:\n"
                f"   python scripts/generate_synthetic_data.py --clear"
            )
            return False
        elif count > 0:
            logger.warning(
                f"⚠️ Database currently has {count} transactions. Exiting to avoid partial state.\n"
                f"   To wipe and regenerate fresh data, run:\n"
                f"   python scripts/generate_synthetic_data.py --clear"
            )
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

    insufficient = sum(
        1
        for t in failed_txns
        if t.failure_reason
        in (FailureReasonType.INSUFFICIENT_FUNDS, FailureReasonType.INSUFFICIENT_FUNDS.value)
    )
    bank_err = sum(
        1
        for t in failed_txns
        if t.failure_reason
        in (FailureReasonType.BANK_ERROR, FailureReasonType.BANK_ERROR.value)
    )
    expired = sum(
        1
        for t in failed_txns
        if t.failure_reason
        in (FailureReasonType.EXPIRED_CARD, FailureReasonType.EXPIRED_CARD.value)
    )
    auth_fail = sum(
        1
        for t in failed_txns
        if t.failure_reason
        in (FailureReasonType.AUTH_FAILURE, FailureReasonType.AUTH_FAILURE.value)
    )
    unknown = sum(
        1
        for t in failed_txns
        if t.failure_reason
        in (FailureReasonType.UNKNOWN, FailureReasonType.UNKNOWN.value)
    )

    upi = sum(
        1
        for t in transactions
        if t.payment_method in (PaymentMethod.UPI, PaymentMethod.UPI.value)
    )
    card = sum(
        1
        for t in transactions
        if t.payment_method in (PaymentMethod.CARD, PaymentMethod.CARD.value)
    )
    netbanking = sum(
        1
        for t in transactions
        if t.payment_method in (PaymentMethod.NETBANKING, PaymentMethod.NETBANKING.value)
    )
    wallet = sum(
        1
        for t in transactions
        if t.payment_method in (PaymentMethod.WALLET, PaymentMethod.WALLET.value)
    )

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


async def generate_data(
    total_transactions: int = DEFAULT_TOTAL_TRANSACTIONS,
    num_failed: int = DEFAULT_FAILED_TRANSACTIONS,
    clear: bool = False,
) -> None:
    """Generate and insert synthetic transaction data."""
    if num_failed > total_transactions:
        raise ValueError(f"Failed count ({num_failed}) cannot exceed total ({total_transactions})")

    # Seed random generators for reproducible generation
    random.seed(42)
    np.random.seed(42)
    Faker.seed(42)
    fake = Faker()

    async with AsyncSessionLocal() as session:
        if clear:
            await clear_existing_data(session)

        if not await check_idempotency(session, total_expected=total_transactions):
            return

        logger.info(
            f"🔧 Generating {total_transactions} transactions "
            f"(target: {total_transactions - num_failed} success, {num_failed} failed)..."
        )

        # Pre-generate UUID pools
        merchant_ids = [uuid.uuid4() for _ in range(MERCHANT_COUNT)]
        customer_ids = [uuid.uuid4() for _ in range(CUSTOMER_COUNT)]

        num_success = total_transactions - num_failed

        # Generate exact failure breakdown matching proportions
        failed_reasons = []
        for reason_type, prop in FAILURE_BREAKDOWN.items():
            count = int(round(num_failed * prop))
            failed_reasons.extend([reason_type.value] * count)

        # Adjust for rounding to hit exact num_failed
        while len(failed_reasons) < num_failed:
            failed_reasons.append(FailureReasonType.UNKNOWN.value)
        while len(failed_reasons) > num_failed:
            failed_reasons.pop()
        random.shuffle(failed_reasons)

        # Generate exact payment method breakdown
        payment_methods = []
        for method, prop in PAYMENT_METHOD_BREAKDOWN.items():
            count = int(round(total_transactions * prop))
            payment_methods.extend([method] * count)

        while len(payment_methods) < total_transactions:
            payment_methods.append(PaymentMethod.UPI)
        while len(payment_methods) > total_transactions:
            payment_methods.pop()
        random.shuffle(payment_methods)

        # Generate log-normal amounts
        amounts = np.random.lognormal(mean=6.5, sigma=1.0, size=total_transactions)
        amounts = np.clip(amounts, 100.0, 50000.0)

        transactions = []
        failure_idx = 0

        for i in range(total_transactions):
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
                updated_at=created_at,
            )
            transactions.append(txn)

        # Shuffle so successes and failures are interspersed chronologically
        random.shuffle(transactions)

        # Batch insert
        try:
            total_batches = (total_transactions + BATCH_SIZE - 1) // BATCH_SIZE
            for i in range(0, total_transactions, BATCH_SIZE):
                batch = transactions[i : i + BATCH_SIZE]
                session.add_all(batch)
                await session.commit()
                batch_num = i // BATCH_SIZE + 1
                logger.info(f"Saving transactions... [batch {batch_num}/{total_batches}]")

            print_summary(transactions)

        except SQLAlchemyError as e:
            await session.rollback()
            logger.error(f"❌ Failed to insert synthetic data: {e}")


def parse_args():
    """Parse command line options."""
    parser = argparse.ArgumentParser(
        description="Generate synthetic transaction data for RazorRecover."
    )
    parser.add_argument(
        "--total",
        type=int,
        default=DEFAULT_TOTAL_TRANSACTIONS,
        help=f"Total transactions to generate (default: {DEFAULT_TOTAL_TRANSACTIONS})",
    )
    parser.add_argument(
        "--failed",
        type=int,
        default=DEFAULT_FAILED_TRANSACTIONS,
        help=f"Number of failed transactions (default: {DEFAULT_FAILED_TRANSACTIONS})",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear existing database tables before generating fresh transactions",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(
        generate_data(
            total_transactions=args.total,
            num_failed=args.failed,
            clear=args.clear,
        )
    )
