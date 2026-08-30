"""
Drishya Circuit Breaker & Retry Engine
=======================================
Provides resilient retry logic with exponential backoff and circuit breaker
pattern for third-party OSINT feeds that may throttle, timeout, or go down.

States:
  CLOSED  → Normal operation, requests pass through.
  OPEN    → Circuit tripped after repeated failures; requests are blocked.
  HALF_OPEN → After cooldown, one probe request is allowed through.
"""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import Any, Callable, Dict, Optional, TypeVar

logger = logging.getLogger("drishya.circuit_breaker")

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when a circuit breaker is in OPEN state and blocks a request."""
    def __init__(self, provider: str, cooldown_remaining: float):
        self.provider = provider
        self.cooldown_remaining = cooldown_remaining
        super().__init__(
            f"Circuit OPEN for provider '{provider}'. "
            f"Retry in {cooldown_remaining:.0f}s."
        )


class CircuitBreaker:
    """
    Per-provider circuit breaker with configurable thresholds.

    Parameters:
        failure_threshold: Number of consecutive failures before opening circuit.
        cooldown_seconds: Time to wait before trying a half-open probe.
        half_open_max_calls: Number of probe calls allowed in half-open state.
    """

    def __init__(
        self,
        provider: str,
        failure_threshold: int = 5,
        cooldown_seconds: float = 60.0,
        half_open_max_calls: int = 1,
    ):
        self.provider = provider
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.half_open_max_calls = half_open_max_calls

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = 0.0
        self._half_open_calls = 0
        self._consecutive_successes = 0

    @property
    def state(self) -> CircuitState:
        """Check if cooldown has elapsed to transition OPEN → HALF_OPEN."""
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self.cooldown_seconds:
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
                logger.info(
                    "[CircuitBreaker:%s] OPEN → HALF_OPEN after %.1fs cooldown",
                    self.provider, elapsed,
                )
        return self._state

    def record_success(self) -> None:
        """Record a successful call."""
        if self._state == CircuitState.HALF_OPEN:
            self._consecutive_successes += 1
            if self._consecutive_successes >= 2:
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._consecutive_successes = 0
                logger.info(
                    "[CircuitBreaker:%s] HALF_OPEN → CLOSED (recovered)",
                    self.provider,
                )
        elif self._state == CircuitState.CLOSED:
            self._failure_count = max(0, self._failure_count - 1)
            self._consecutive_successes += 1

    def record_failure(self) -> None:
        """Record a failed call."""
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        self._consecutive_successes = 0

        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            logger.warning(
                "[CircuitBreaker:%s] HALF_OPEN → OPEN (probe failed)",
                self.provider,
            )
        elif self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            logger.warning(
                "[CircuitBreaker:%s] CLOSED → OPEN after %d failures",
                self.provider, self._failure_count,
            )

    def can_execute(self) -> bool:
        """Check if a request can pass through the circuit breaker."""
        current_state = self.state  # triggers state transition check
        if current_state == CircuitState.CLOSED:
            return True
        if current_state == CircuitState.HALF_OPEN:
            if self._half_open_calls < self.half_open_max_calls:
                self._half_open_calls += 1
                return True
            return False
        return False  # OPEN

    def get_cooldown_remaining(self) -> float:
        """Return seconds remaining in cooldown."""
        if self._state != CircuitState.OPEN:
            return 0.0
        elapsed = time.monotonic() - self._last_failure_time
        return max(0.0, self.cooldown_seconds - elapsed)

    def reset(self) -> None:
        """Manually reset the circuit breaker to CLOSED."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._consecutive_successes = 0

    def get_status(self) -> dict:
        return {
            "provider": self.provider,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "cooldown_remaining": round(self.get_cooldown_remaining(), 1),
        }


# ─── Circuit Breaker Registry ────────────────────────────────────────────────

class CircuitBreakerRegistry:
    """Global registry of circuit breakers keyed by provider name."""

    def __init__(self):
        self._breakers: Dict[str, CircuitBreaker] = {}

    def get_or_create(
        self,
        provider: str,
        failure_threshold: int = 5,
        cooldown_seconds: float = 60.0,
    ) -> CircuitBreaker:
        if provider not in self._breakers:
            self._breakers[provider] = CircuitBreaker(
                provider=provider,
                failure_threshold=failure_threshold,
                cooldown_seconds=cooldown_seconds,
            )
        return self._breakers[provider]

    def get_all_status(self) -> Dict[str, dict]:
        return {
            name: breaker.get_status()
            for name, breaker in self._breakers.items()
        }

    def reset_all(self) -> None:
        for breaker in self._breakers.values():
            breaker.reset()


circuit_registry = CircuitBreakerRegistry()


# ─── Exponential Backoff Retry ───────────────────────────────────────────────

async def retry_with_backoff(
    func: Callable[..., Any],
    *args,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: bool = True,
    provider: str = "unknown",
    **kwargs,
) -> Any:
    """
    Execute an async function with exponential backoff retry.

    Integrates with the circuit breaker registry for the given provider.
    Raises the last exception if all retries are exhausted.
    """
    breaker = circuit_registry.get_or_create(provider)

    if not breaker.can_execute():
        cooldown = breaker.get_cooldown_remaining()
        logger.warning(
            "[Retry:%s] Circuit breaker OPEN, skipping request (%.0fs remaining)",
            provider, cooldown,
        )
        raise CircuitOpenError(provider, cooldown)

    last_exception = None
    for attempt in range(max_retries + 1):
        try:
            result = await func(*args, **kwargs)
            breaker.record_success()
            return result
        except CircuitOpenError:
            raise
        except Exception as exc:
            last_exception = exc
            breaker.record_failure()

            if attempt == max_retries:
                break

            delay = min(base_delay * (2 ** attempt), max_delay)
            if jitter:
                import random
                delay *= (0.5 + random.random() * 0.5)

            logger.warning(
                "[Retry:%s] Attempt %d/%d failed: %s. Retrying in %.1fs",
                provider, attempt + 1, max_retries + 1, str(exc)[:100], delay,
            )
            await asyncio.sleep(delay)

    raise last_exception  # type: ignore[misc]


# ─── Provider Health Monitor ─────────────────────────────────────────────────

class ProviderHealthMonitor:
    """
    Tracks health metrics for each provider: latency, success rate, error patterns.
    Provides a dashboard-ready status summary.
    """

    def __init__(self):
        self._metrics: Dict[str, dict] = {}

    def record_request(self, provider: str, duration_ms: float, success: bool) -> None:
        if provider not in self._metrics:
            self._metrics[provider] = {
                "total_requests": 0,
                "successes": 0,
                "failures": 0,
                "total_latency_ms": 0.0,
                "max_latency_ms": 0.0,
                "last_request_time": 0.0,
                "last_error": None,
            }

        m = self._metrics[provider]
        m["total_requests"] += 1
        m["total_latency_ms"] += duration_ms
        m["max_latency_ms"] = max(m["max_latency_ms"], duration_ms)
        m["last_request_time"] = time.time()

        if success:
            m["successes"] += 1
        else:
            m["failures"] += 1

    def record_error(self, provider: str, error: str) -> None:
        if provider in self._metrics:
            self._metrics[provider]["last_error"] = error

    def get_provider_status(self, provider: str) -> dict:
        m = self._metrics.get(provider, {})
        total = m.get("total_requests", 0)
        successes = m.get("successes", 0)
        return {
            "provider": provider,
            "total_requests": total,
            "success_rate": round(successes / max(total, 1), 3),
            "avg_latency_ms": round(
                m.get("total_latency_ms", 0) / max(total, 1), 1
            ),
            "max_latency_ms": round(m.get("max_latency_ms", 0), 1),
            "last_error": m.get("last_error"),
            "circuit_breaker": circuit_registry.get_or_create(provider).get_status(),
        }

    def get_all_status(self) -> Dict[str, dict]:
        return {
            provider: self.get_provider_status(provider)
            for provider in set(
                list(self._metrics.keys()) +
                list(circuit_registry._breakers.keys())
            )
        }


health_monitor = ProviderHealthMonitor()
