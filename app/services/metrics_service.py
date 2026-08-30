"""Metrics service."""
from __future__ import annotations

class MetricsService:
    """Computes and aggregates recovery metrics across all transactions."""

    def __init__(self) -> None:
        """Initialize the MetricsService."""
        pass

    async def compute_recovery_metrics(self) -> dict:
        """Compute recovery metrics."""
        raise NotImplementedError
