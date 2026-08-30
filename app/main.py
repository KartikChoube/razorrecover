"""RazorRecover main application."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

import app.config  # Import and configure logging from app.config
from app.database import async_engine, init_db
from app.routers import classification, interventions, metrics, transactions

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context manager."""
    logger.info("RazorRecover starting up...")
    await init_db()
    yield
    logger.info("RazorRecover shutting down...")
    await async_engine.dispose()

app = FastAPI(
    title="RazorRecover",
    description="AI-Powered Failed Payment Recovery Agent",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(transactions.router, prefix="/api/v1")
app.include_router(classification.router, prefix="/api/v1")
app.include_router(interventions.router, prefix="/api/v1")
app.include_router(metrics.router, prefix="/api/v1")

@app.get("/")
async def root() -> dict:
    """Get basic application info."""
    return {
        "name": app.title,
        "version": app.version,
        "status": "operational",
        "docs_url": "/docs",
    }

@app.get("/health")
async def health() -> dict:
    """Health check endpoint."""
    return {"status": "healthy", "version": "0.1.0"}
