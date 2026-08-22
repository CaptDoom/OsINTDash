"""Deterministic country-risk scoring built from the stored news evidence."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable


RISK_LEVELS = ("Low", "Moderate", "High", "Critical")
NEGATIVE_TERMS = re.compile(
    r"\b(attack|airstrike|casualt|clash|conflict|crisis|detention|drone|explosion|missile|protest|raid|sanction|troop|violence|war)\b",
    re.IGNORECASE,
)


def _level(score: float) -> str:
    if score >= 75:
        return "Critical"
    if score >= 50:
        return "High"
    if score >= 25:
        return "Moderate"
    return "Low"


def calculate_country_risk(articles: Iterable[Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Return a transparent 0-100 risk score and explainable contributing factors."""
    current = now or datetime.now(timezone.utc)
    rows = list(articles)
    if not rows:
        return {
            "score": 0,
            "level": "Low",
            "trend": "stable",
            "factors": [],
            "article_count": 0,
            "source_count": 0,
        }

    weighted_event_score = 0.0
    recent_count = 0
    previous_count = 0
    negative_count = 0
    sources = set()
    impact_counts = Counter()

    for article in rows:
        published = getattr(article, "published_at", None) or current
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (current - published).total_seconds() / 86400)
        recency_weight = max(0.15, 1.0 - min(age_days, 30.0) / 36.0)
        impact = getattr(article, "impact_level", "Normal Impact")
        impact_weight = {"High Impact": 28, "Medium Impact": 14}.get(impact, 4)
        text = f"{getattr(article, 'title', '')} {getattr(article, 'summary', '') or getattr(article, 'content', '')}"
        negative = bool(NEGATIVE_TERMS.search(text))
        if negative:
            negative_count += 1
        if age_days <= 7:
            recent_count += 1
        elif age_days <= 30:
            previous_count += 1
        source = getattr(article, "source", None)
        if source:
            sources.add(source)
        impact_counts[impact] += 1
        weighted_event_score += (impact_weight + (5 if negative else 0)) * recency_weight

    score = min(100.0, weighted_event_score / max(1, len(rows)) * 2.2)
    if len(sources) >= 3:
        score += 5
    elif len(sources) == 2:
        score += 2
    score = round(min(100.0, score), 1)

    trend = "rising" if recent_count > previous_count * 1.25 and recent_count >= 2 else "falling" if previous_count > recent_count * 1.5 else "stable"
    factors = []
    if impact_counts["High Impact"]:
        factors.append(f"{impact_counts['High Impact']} high-impact reports")
    if negative_count:
        factors.append(f"{negative_count} reports contain escalation indicators")
    if len(sources) > 1:
        factors.append(f"Evidence spans {len(sources)} sources")
    if trend == "rising":
        factors.append("Seven-day report volume is rising")

    return {
        "score": score,
        "level": _level(score),
        "trend": trend,
        "factors": factors,
        "article_count": len(rows),
        "source_count": len(sources),
        "recent_article_count": recent_count,
        "impact_counts": dict(impact_counts),
    }
