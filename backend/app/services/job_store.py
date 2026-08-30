from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, Optional

from backend.app.config import settings

logger = logging.getLogger("drishya.job_store")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class JobRecord:
    job_id: str
    job_type: str
    status: str
    progress: int = 0
    step: str = "queued"
    payload: Dict[str, Any] = field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)


class JobStore:
    def __init__(self) -> None:
        self._memory_jobs: Dict[str, JobRecord] = {}
        self._memory_subscribers: set[asyncio.Queue[Dict[str, Any]]] = set()

    async def _get_redis(self):
        try:
            from backend.app.redis_pool import get_redis_pool
            return await get_redis_pool()
        except Exception as exc:
            logger.warning("Redis job store unavailable, using memory fallback: %s", exc)
            return None

    @staticmethod
    def _key(job_type: str, job_id: str) -> str:
        return f"drishya:job:{job_type}:{job_id}"

    @staticmethod
    def _channel(job_type: str) -> str:
        return f"drishya:jobs:{job_type}"

    @staticmethod
    def _to_redis_mapping(job: JobRecord) -> Dict[str, str]:
        mapping = {}
        for k, v in asdict(job).items():
            if v is None:
                continue
            if isinstance(v, (dict, list)):
                mapping[k] = json.dumps(v)
            else:
                mapping[k] = str(v)
        return mapping

    async def create(self, job_type: str, payload: Dict[str, Any]) -> JobRecord:
        job = JobRecord(
            job_id=payload.get("job_id") or payload.get("id") or f"{job_type}-{datetime.now().timestamp():.0f}",
            job_type=job_type,
            status="queued",
            progress=0,
            step="queued",
            payload=payload,
        )
        redis_conn = await self._get_redis()
        if redis_conn:
            await redis_conn.hset(self._key(job_type, job.job_id), mapping=self._to_redis_mapping(job))
            await redis_conn.publish(self._channel(job_type), json.dumps(asdict(job)))
        else:
            self._memory_jobs[job.job_id] = job
            await self._broadcast_memory(job)
        return job

    async def update(self, job: JobRecord | str, job_type: Optional[str] = None, **fields: Any) -> JobRecord:
        if isinstance(job, str):
            if not job_type:
                raise ValueError("job_type is required when job id is provided")
            current = await self.get(job_type, job)
            if current is None:
                current = JobRecord(job_id=job, job_type=job_type, status="queued")
        else:
            current = job

        for key, value in fields.items():
            if hasattr(current, key):
                setattr(current, key, value)
        current.updated_at = _utc_now()

        redis_conn = await self._get_redis()
        if redis_conn:
            await redis_conn.hset(
                self._key(current.job_type, current.job_id),
                mapping=self._to_redis_mapping(current),
            )
            await redis_conn.publish(self._channel(current.job_type), json.dumps(asdict(current)))
        else:
            self._memory_jobs[current.job_id] = current
            await self._broadcast_memory(current)
        return current

    async def get(self, job_type: str, job_id: str) -> Optional[JobRecord]:
        redis_conn = await self._get_redis()
        if redis_conn:
            raw = await redis_conn.hgetall(self._key(job_type, job_id))
            if not raw:
                return None
            payload = raw.get("payload")
            parsed_payload = json.loads(payload) if payload else {}
            return JobRecord(
                job_id=raw["job_id"],
                job_type=raw["job_type"],
                status=raw["status"],
                progress=int(raw.get("progress", 0)),
                step=raw.get("step", "queued"),
                payload=parsed_payload,
                result=json.loads(raw["result"]) if raw.get("result") else None,
                error=raw.get("error"),
                created_at=raw.get("created_at", _utc_now()),
                updated_at=raw.get("updated_at", _utc_now()),
            )
        return self._memory_jobs.get(job_id)

    async def subscribe(self, job_type: str) -> AsyncIterator[Dict[str, Any]]:
        redis_conn = await self._get_redis()
        if redis_conn:
            pubsub = redis_conn.pubsub()
            await pubsub.subscribe(self._channel(job_type))
            try:
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    yield json.loads(message["data"])
            finally:
                await pubsub.unsubscribe(self._channel(job_type))
        else:
            queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
            self._memory_subscribers.add(queue)
            try:
                while True:
                    yield await queue.get()
            finally:
                self._memory_subscribers.discard(queue)

    async def _broadcast_memory(self, job: JobRecord) -> None:
        payload = asdict(job)
        for queue in list(self._memory_subscribers):
            try:
                queue.put_nowait(payload)
            except Exception:
                continue


job_store = JobStore()
