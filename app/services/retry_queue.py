"""
Redis-based retry job queue for scheduled payment retries.

Uses a Redis sorted set where:
- Members are serialized job payloads (JSON with transaction_id, intervention_id)
- Scores are Unix timestamps of when the job should execute

This is NOT a real background scheduler — jobs are processed when the
POST /interventions/process-due-retries endpoint is called manually.
For a production system, use Celery, APScheduler, or similar.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List
from uuid import UUID

import redis.asyncio as redis

from app.config import settings

logger = logging.getLogger(__name__)


class RetryQueue:
    """Redis-based queue for managing delayed retry tasks."""
    
    QUEUE_KEY = "razorrecover:retry_queue"
    
    def __init__(self):
        try:
            self.redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        except Exception as e:
            logger.warning(f"Failed to initialize Redis client: {e}")
            self.redis_client = None

    async def enqueue(self, transaction_id: UUID, intervention_id: int, execute_at: datetime) -> bool:
        """
        Enqueue a new retry job.
        
        Args:
            transaction_id: The UUID of the transaction.
            intervention_id: The ID of the intervention triggering this retry.
            execute_at: The datetime when this job should be executed.
            
        Returns:
            True if successfully enqueued, False otherwise.
        """
        if not self.redis_client:
            logger.error("Redis client not initialized.")
            return False
            
        try:
            score = execute_at.timestamp()
            payload = json.dumps({
                "transaction_id": str(transaction_id),
                "intervention_id": intervention_id
            })
            
            await self.redis_client.zadd(self.QUEUE_KEY, {payload: score})
            logger.info(f"Enqueued retry job for transaction {transaction_id} at {execute_at}")
            return True
        except Exception as e:
            logger.error(f"Failed to enqueue retry job: {e}")
            return False

    async def get_due_jobs(self) -> List[Dict[str, Any]]:
        """
        Retrieve all jobs that are due for execution (score <= current timestamp).
        
        Returns:
            A list of dictionary payloads for due jobs.
        """
        if not self.redis_client:
            return []
            
        try:
            now_ts = datetime.now().timestamp()
            members = await self.redis_client.zrangebyscore(self.QUEUE_KEY, 0, now_ts)
            
            jobs = []
            for member in members:
                try:
                    job_data = json.loads(member)
                    # Keep raw member for easy deletion later
                    job_data["_raw_payload"] = member
                    jobs.append(job_data)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to decode job payload: {member}")
                    
            return jobs
        except Exception as e:
            logger.error(f"Failed to get due jobs: {e}")
            return []

    async def remove_job(self, job_payload: str) -> bool:
        """
        Remove a specific job from the queue using its raw payload string.
        
        Args:
            job_payload: The raw JSON string of the job to remove.
            
        Returns:
            True if removed successfully, False otherwise.
        """
        if not self.redis_client:
            return False
            
        try:
            removed = await self.redis_client.zrem(self.QUEUE_KEY, job_payload)
            return removed > 0
        except Exception as e:
            logger.error(f"Failed to remove job: {e}")
            return False

    async def get_queue_size(self) -> int:
        """
        Get the total number of jobs in the queue.
        
        Returns:
            The number of jobs.
        """
        if not self.redis_client:
            return 0
            
        try:
            return await self.redis_client.zcard(self.QUEUE_KEY)
        except Exception as e:
            logger.error(f"Failed to get queue size: {e}")
            return 0

    async def get_pending_jobs(self) -> List[Dict[str, Any]]:
        """
        Get all jobs in the queue along with their scheduled execution times.
        
        Returns:
            A list of dictionaries with job data and schedule times.
        """
        if not self.redis_client:
            return []
            
        try:
            # WITHSCORES returns a list of tuples (member, score)
            members_with_scores = await self.redis_client.zrange(
                self.QUEUE_KEY, 0, -1, withscores=True
            )
            
            jobs = []
            for member, score in members_with_scores:
                try:
                    job_data = json.loads(member)
                    job_data["execute_at_timestamp"] = score
                    jobs.append(job_data)
                except json.JSONDecodeError:
                    pass
                    
            return jobs
        except Exception as e:
            logger.error(f"Failed to get pending jobs: {e}")
            return []
