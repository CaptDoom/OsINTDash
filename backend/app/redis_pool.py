"""
Shared Redis connection pool for the Drishya backend.

Provides a single, reusable async Redis connection pool used across all modules
(auth, cache, classifier, pub/sub, job store, etc.) to eliminate per-request
connection creation overhead.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

import redis.asyncio as aioredis

from backend.app.config import settings

logger = logging.getLogger("drishya.redis_pool")

_pool: Optional[aioredis.Redis] = None
_lock = asyncio.Lock()


async def get_redis_pool() -> Optional[aioredis.Redis]:
    """Return a shared Redis connection pool, creating it lazily on first use."""
    global _pool
    if _pool is not None:
        try:
            await _pool.ping()
            return _pool
        except Exception:
            # Connection is stale; tear down and recreate below
            try:
                await _pool.aclose()
            except Exception:
                pass
            _pool = None

    async with _lock:
        if _pool is not None:
            try:
                await _pool.ping()
                return _pool
            except Exception:
                try:
                    await _pool.aclose()
                except Exception:
                    pass
                _pool = None

        try:
            _pool = aioredis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_timeout=3.0,
                socket_connect_timeout=3.0,
                max_connections=20,
                retry_on_timeout=True,
            )
            await _pool.ping()
            logger.info("[Redis] Shared connection pool created successfully.")
            return _pool
        except Exception as exc:
            logger.warning("[Redis] Failed to create connection pool: %s", exc)
            _pool = None
            return None


async def close_redis_pool() -> None:
    """Gracefully close the shared Redis connection pool."""
    global _pool
    if _pool is not None:
        try:
            await _pool.aclose()
        except Exception:
            pass
        _pool = None


async def cache_get(key: str) -> Optional[Any]:
    """Get a cached value by key, returning None on miss or Redis failure."""
    pool = await get_redis_pool()
    if not pool:
        return None
    try:
        val = await pool.get(key)
        if val is not None:
            try:
                return json.loads(val)
            except (json.JSONDecodeError, TypeError):
                return val
    except Exception as exc:
        logger.debug("[Redis] cache_get failed for %s: %s", key, exc)
    return None


async def cache_set(key: str, data: Any, ttl: int = 300) -> bool:
    """Set a cached value with TTL. Returns True on success."""
    pool = await get_redis_pool()
    if not pool:
        return False
    try:
        serialized = json.dumps(data, default=str)
        await pool.set(key, serialized, ex=ttl)
        return True
    except Exception as exc:
        logger.debug("[Redis] cache_set failed for %s: %s", key, exc)
    return False


async def cache_delete(pattern: str) -> int:
    """Delete keys matching a pattern. Returns count of deleted keys."""
    pool = await get_redis_pool()
    if not pool:
        return 0
    try:
        deleted = 0
        async for key in pool.scan_iter(match=pattern):
            await pool.delete(key)
            deleted += 1
        return deleted
    except Exception as exc:
        logger.debug("[Redis] cache_delete failed for %s: %s", pattern, exc)
    return 0


async def pubsub_publish(channel: str, message: dict) -> bool:
    """Publish a message to a Redis pub/sub channel."""
    pool = await get_redis_pool()
    if not pool:
        return False
    try:
        await pool.publish(channel, json.dumps(message, default=str))
        return True
    except Exception as exc:
        logger.debug("[Redis] pubsub_publish failed on %s: %s", channel, exc)
    return False
