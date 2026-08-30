"""
Credibility & Corroboration Status API
Exposes the cross-corroboration engine status and provider health to the dashboard.
"""

from __future__ import annotations

import logging
from fastapi import APIRouter, Query, HTTPException

logger = logging.getLogger("drishya.api.credibility")

router = APIRouter(prefix="/api/credibility", tags=["credibility"])


@router.get("/status")
async def credibility_status():
    """
    Return the current state of the cross-corroboration engine,
    verified stories, and provider health metrics.
    """
    from backend.app.services.credibility import corroboration_engine
    from backend.app.services.circuit_breaker import health_monitor, circuit_registry

    verified = corroboration_engine.get_verified_stories(min_score=0.3)
    all_stories = list(corroboration_engine.stories.values())

    provider_health = health_monitor.get_all_status()
    circuit_status = circuit_registry.get_all_status()

    # Aggregate stats
    total_sources = sum(s.unique_source_count for s in all_stories)
    avg_score = (
        sum(s.corroboration_score for s in all_stories) / max(len(all_stories), 1)
    )

    return {
        "engine": {
            "total_tracked_stories": len(all_stories),
            "verified_stories": len(verified),
            "total_cross_referenced_sources": total_sources,
            "avg_corroboration_score": round(avg_score, 3),
            "min_sources_for_verification": corroboration_engine.min_sources,
        },
        "verified_stories": [s.to_dict() for s in verified[:20]],
        "provider_health": provider_health,
        "circuit_breakers": circuit_status,
    }


@router.get("/story/{story_key}")
async def get_story_detail(story_key: str):
    """Return detailed corroboration info for a specific story."""
    from backend.app.services.credibility import corroboration_engine

    story = corroboration_engine.get_story_by_key(story_key)
    if not story:
        raise HTTPException(status_code=404, detail="Story not found")
    return story.to_dict()


@router.get("/provider-health")
async def provider_health():
    """Return health metrics for all OSINT providers."""
    from backend.app.services.circuit_breaker import health_monitor
    return health_monitor.get_all_status()


@router.post("/reset-circuits")
async def reset_all_circuits():
    """Manually reset all circuit breakers (admin operation)."""
    from backend.app.services.circuit_breaker import circuit_registry
    circuit_registry.reset_all()
    logger.warning("[Credibility] All circuit breakers manually reset.")
    return {"status": "all_circuits_reset"}
