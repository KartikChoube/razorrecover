from __future__ import annotations

from enum import Enum


class TransactionStatus(str, Enum):
    """Status of a transaction."""
    SUCCESS = "success"
    FAILED = "failed"
    PENDING = "pending"


class PaymentMethod(str, Enum):
    """Payment method used."""
    UPI = "upi"
    CARD = "card"
    NETBANKING = "netbanking"
    WALLET = "wallet"


class FailureReasonType(str, Enum):
    """Classified reason for a failed payment."""
    INSUFFICIENT_FUNDS = "insufficient_funds"
    BANK_ERROR = "bank_error"
    EXPIRED_CARD = "expired_card"
    AUTH_FAILURE = "auth_failure"
    UNKNOWN = "unknown"


class InterventionType(str, Enum):
    """Type of action to recover payment."""
    AUTO_RETRY = "auto_retry"
    CUSTOMER_NOTIFICATION = "customer_notification"
    MANUAL_ESCALATION = "manual_escalation"
    NO_ACTION = "no_action"


class InterventionStatus(str, Enum):
    """Status of an intervention action."""
    PENDING = "pending"
    EXECUTED = "executed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ESCALATED = "escalated"


class ActorType(str, Enum):
    """Type of actor triggering an event."""
    SYSTEM = "system"
    AI = "ai"
    HUMAN = "human"
