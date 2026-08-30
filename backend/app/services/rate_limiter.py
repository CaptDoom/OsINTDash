"""
Drishya Rate Limiter
====================
Sliding window rate limiting middleware for FastAPI/Uvicorn.

Protects the backend from API exhaustion and brute-force attacks.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import Dict, Optional, Tuple

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger("drishya.rate_limiter")


class SlidingWindowRateLimiter:
    """
    In-memory sliding window rate limiter.

    For production with multiple workers, swap the in-memory dict
    for Redis-backed counters.
    """

    def __init__(self, window_seconds: int = 60, max_requests: int = 120):
        self.window_seconds = window_seconds
        self.max_requests = max_requests
        self._requests: Dict[str, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def is_allowed(self, key: str) -> Tuple[bool, Dict[str, str]]:
        """
        Check if a request is allowed for the given key.

        Returns (allowed, headers_dict) where headers include
        X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset.
        """
        async with self._lock:
            now = time.time()
            window_start = now - self.window_seconds

            # Prune old entries
            self._requests[key] = [
                ts for ts in self._requests[key] if ts > window_start
            ]

            current_count = len(self._requests[key])
            remaining = max(0, self.max_requests - current_count)
            reset_at = int(window_start + self.window_seconds) + 1

            headers = {
                "X-RateLimit-Limit": str(self.max_requests),
                "X-RateLimit-Remaining": str(remaining),
                "X-RateLimit-Reset": str(reset_at),
            }

            if current_count >= self.max_requests:
                retry_after = max(1, int(self._requests[key][0] + self.window_seconds - now))
                headers["Retry-After"] = str(retry_after)
                return False, headers

            self._requests[key].append(now)
            return True, headers

    def cleanup(self, max_age: float = 7200) -> int:
        """Remove entries older than max_age seconds. Returns count removed."""
        cutoff = time.time() - max_age
        removed = 0
        for key in list(self._requests.keys()):
            self._requests[key] = [ts for ts in self._requests[key] if ts > cutoff]
            if not self._requests[key]:
                del self._requests[key]
                removed += 1
        return removed


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware that enforces per-IP rate limiting.

    Different limits for different route categories:
    - Auth routes: stricter (10 req/min) to prevent brute force
    - API routes: standard (120 req/min)
    - Health/readiness: exempt
    """

    # Route-based limits: (prefix, max_requests_per_minute)
    ROUTE_LIMITS = [
        ("/api/auth/login", 10),
        ("/api/auth/verify_mfa", 10),
        ("/api/news/refresh", 5),
        ("/api/scrape", 10),
    ]

    DEFAULT_LIMIT = 120
    EXEMPT_PREFIXES = ["/health", "/ready", "/metrics", "/ws"]

    def __init__(self, app, window_seconds: int = 60):
        super().__init__(app)
        self.window = window_seconds
        self._limiters: Dict[int, SlidingWindowRateLimiter] = {}

    def _get_limiter(self, max_requests: int) -> SlidingWindowRateLimiter:
        if max_requests not in self._limiters:
            self._limiters[max_requests] = SlidingWindowRateLimiter(
                window_seconds=self.window,
                max_requests=max_requests,
            )
        return self._limiters[max_requests]

    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP, respecting X-Forwarded-For behind proxies."""
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
        if request.client:
            return request.client.host
        return "unknown"

    def _get_route_limit(self, path: str) -> int:
        """Determine rate limit for a given path."""
        for prefix, limit in self.ROUTE_LIMITS:
            if path.startswith(prefix):
                return limit
        return self.DEFAULT_LIMIT

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Exempt health/readiness/metrics/websocket endpoints
        for prefix in self.EXEMPT_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        client_ip = self._get_client_ip(request)
        route_limit = self._get_route_limit(path)
        limiter = self._get_limiter(route_limit)

        key = f"{client_ip}:{path.rsplit('/', 1)[0] if '/' in path else path}"
        allowed, headers = await limiter.is_allowed(key)

        if not allowed:
            logger.warning(
                "[RateLimit] %s blocked (%s) — limit %d/min on %s",
                client_ip, path, route_limit, path,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded. Please wait before retrying.",
                    "retry_after": headers.get("Retry-After", "60"),
                },
                headers=headers,
            )

        response = await call_next(request)
        for header_key, header_val in headers.items():
            response.headers[header_key] = header_val
        return response


# ─── Brute Force Protection for Auth ────────────────────────────────────────

class BruteForceProtector:
    """
    Tracks failed login attempts per IP and enforces escalating lockouts.
    """

    def __init__(
        self,
        max_attempts: int = 5,
        lockout_base_seconds: float = 300,
        lockout_max_seconds: float = 3600,
    ):
        self.max_attempts = max_attempts
        self.lockout_base = lockout_base_seconds
        self.lockout_max = lockout_max_seconds
        self._failures: Dict[str, list[float]] = defaultdict(list)
        self._lockouts: Dict[str, float] = {}  # ip -> unlock_time

    def record_failure(self, ip: str) -> None:
        now = time.time()
        self._failures[ip].append(now)

        # Prune old entries
        self._failures[ip] = [t for t in self._failures[ip] if t > now - 3600]

        if len(self._failures[ip]) >= self.max_attempts:
            failures_count = len(self._failures[ip])
            lockout_duration = min(
                self.lockout_base * (2 ** (failures_count - self.max_attempts)),
                self.lockout_max,
            )
            self._lockouts[ip] = now + lockout_duration
            logger.warning(
                "[BruteForce] IP %s locked out for %.0fs after %d failures",
                ip, lockout_duration, failures_count,
            )

    def record_success(self, ip: str) -> None:
        self._failures.pop(ip, None)
        self._lockouts.pop(ip, None)

    def is_locked(self, ip: str) -> Tuple[bool, float]:
        """Returns (is_locked, seconds_remaining)."""
        unlock_time = self._lockouts.get(ip, 0)
        now = time.time()
        if now < unlock_time:
            return True, unlock_time - now
        if unlock_time > 0:
            # Lockout expired
            self._lockouts.pop(ip, None)
        return False, 0.0


brute_force_protector = BruteForceProtector()
