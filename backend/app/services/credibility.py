"""
Drishya Credibility Engine
==========================
Cross-corroboration pipeline + source reputation scoring for OSINT intelligence.

Forces at least N independent sources before escalating a threat level,
dramatically increasing dashboard operational value by separating signal from noise.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("drishya.credibility")


# ─── Source Reputation Scoring ───────────────────────────────────────────────

class SourceTier(str, Enum):
    """Tier 1-5 classification of source reliability."""
    TIER_1_WIRE = "tier_1_wire"          # Reuters, AP, AFP
    TIER_2_MAJOR = "tier_2_major"        # BBC, NYT, Guardian, Al Jazeera
    TIER_3_REGIONAL = "tier_3_regional"  # Regional outlets, national press
    TIER_4_AGGREGATOR = "tier_4_aggregator"  # Yahoo, Google News, RSS aggregators
    TIER_5_UNVERIFIED = "tier_5_unverified"  # Unknown domains, social media


TIER_SCORES = {
    SourceTier.TIER_1_WIRE: 1.0,
    SourceTier.TIER_2_MAJOR: 0.85,
    SourceTier.TIER_3_REGIONAL: 0.65,
    SourceTier.TIER_4_AGGREGATOR: 0.40,
    SourceTier.TIER_5_UNVERIFIED: 0.20,
}

TIER_1_SOURCES = {
    "reuters.com", "apnews.com", "reuters", "apnews", "afp.com",
    "dpa.com", "afp", "reuters wire", "associated press",
}

TIER_2_SOURCES = {
    "bbc.com", "bbc.co.uk", "aljazeera.com", "nytimes.com", "theguardian.com",
    "bloomberg.com", "cnn.com", "washingtonpost.com", "ft.com", "economist.com",
    "dw.com", "france24.com", "wsj.com", "foreignaffairs.com",
}

TIER_3_SOURCES = {
    "thehindu.com", "indianexpress.com", "timesofindia", "ndtv.com",
    "hindustantimes.com", "livemint.com", "scmp.com", "globaltimes.cn",
    "xinhua", "tass.com", "interfax.com", "dawn.com", "geo.tv",
    "koreaherald.com", "japantimes.co.jp", "straitstimes.com",
    "app.com.pk", "anadolu.com.tr", "bangkokpost.com",
}

TIER_4_SOURCES = {
    "yahoo.com", "msn.com", "google.com", "news.google.com", "reddit.com",
    "feedburner", "rss", "aggregator", "news aggregator",
}


def classify_source_tier(source: Optional[str]) -> SourceTier:
    """Classify a source into a reliability tier."""
    if not source:
        return SourceTier.TIER_5_UNVERIFIED

    src = source.lower().strip()

    for known in TIER_1_SOURCES:
        if known in src:
            return SourceTier.TIER_1_WIRE

    for known in TIER_2_SOURCES:
        if known in src:
            return SourceTier.TIER_2_MAJOR

    for known in TIER_3_SOURCES:
        if known in src:
            return SourceTier.TIER_3_REGIONAL

    for known in TIER_4_SOURCES:
        if known in src:
            return SourceTier.TIER_4_AGGREGATOR

    return SourceTier.TIER_5_UNVERIFIED


def compute_source_reputation_score(source: Optional[str]) -> float:
    """Return a 0.0-1.0 reputation score for a source."""
    tier = classify_source_tier(source)
    return TIER_SCORES[tier]


def compute_source_reputation_label(source: Optional[str]) -> str:
    """Return human-readable reputation label."""
    tier = classify_source_tier(source)
    labels = {
        SourceTier.TIER_1_WIRE: "Verified Source",
        SourceTier.TIER_2_MAJOR: "Verified Source",
        SourceTier.TIER_3_REGIONAL: "Developing",
        SourceTier.TIER_4_AGGREGATOR: "Developing",
        SourceTier.TIER_5_UNVERIFIED: "Unverified",
    }
    return labels[tier]


# ─── Deduplication Engine ────────────────────────────────────────────────────

def _normalize_text(text: str) -> str:
    """Normalize text for deduplication comparison."""
    t = text.lower().strip()
    t = re.sub(r'[^\w\s]', ' ', t)
    t = re.sub(r'\s+', ' ', t)
    return t


def _text_similarity(a: str, b: str) -> float:
    """Compute similarity ratio between two normalized texts."""
    na, nb = _normalize_text(a), _normalize_text(b)
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def _headline_key(headline: str) -> str:
    """Generate a dedup key from a headline by removing minor variations."""
    normalized = _normalize_text(headline)
    words = normalized.split()
    # Take first 6 significant words as key
    significant = [w for w in words if len(w) > 2][:6]
    return " ".join(significant)


def deduplicate_articles(articles: List[dict], similarity_threshold: float = 0.78) -> List[dict]:
    """
    Deduplicate a list of articles using headline similarity and URL dedup.
    Returns unique articles, keeping the earliest or highest-reputation version.
    """
    if not articles:
        return []

    seen_urls: Dict[str, int] = {}  # url -> index in unique list
    seen_keys: Dict[str, int] = {}  # headline key -> index
    unique: List[dict] = []

    for article in articles:
        url = (article.get("url") or "").strip()
        headline = article.get("title") or article.get("headline") or ""

        # URL exact dedup
        if url and url in seen_urls:
            existing = unique[seen_urls[url]]
            # Keep the one with higher reputation
            existing_rep = existing.get("confidence_score", 0)
            new_rep = article.get("confidence_score", 0)
            if new_rep > existing_rep:
                unique[seen_urls[url]] = article
            continue

        # Headline similarity dedup
        key = _headline_key(headline)
        if key and key in seen_keys:
            existing = unique[seen_keys[key]]
            existing_headline = existing.get("title") or existing.get("headline") or ""
            sim = _text_similarity(headline, existing_headline)
            if sim >= similarity_threshold:
                existing_rep = existing.get("confidence_score", 0)
                new_rep = article.get("confidence_score", 0)
                if new_rep > existing_rep:
                    unique[seen_keys[key]] = article
                continue

        # New unique article
        idx = len(unique)
        unique.append(article)
        if url:
            seen_urls[url] = idx
        if key:
            seen_keys[key] = idx

    return unique


# ─── Cross-Corroboration Pipeline ────────────────────────────────────────────

class CorroborationStatus(str, Enum):
    UNVERIFIED = "unverified"
    SINGLE_SOURCE = "single_source"
    CROSS_REFERENCED = "cross_referenced"
    VERIFIED = "verified"
    DISPUTED = "disputed"


class CorroboratedStory:
    """Represents a story that has been cross-referenced across sources."""

    def __init__(self, story_key: str, headline: str, summary: str):
        self.story_key = story_key
        self.headline = headline
        self.summary = summary
        self.sources: List[dict] = []
        self.status: CorroborationStatus = CorroborationStatus.SINGLE_SOURCE
        self.first_seen: datetime = datetime.now(timezone.utc)
        self.last_updated: datetime = datetime.now(timezone.utc)
        self.corroboration_score: float = 0.0
        self.unique_source_count: int = 0

    def add_source(self, source_data: dict) -> None:
        """Add a corroborating source to this story."""
        source_name = source_data.get("source", "unknown")
        source_url = source_data.get("url", "")

        # Avoid duplicate source domains
        existing_domains = set()
        for s in self.sources:
            domain = self._extract_domain(s.get("url", ""))
            existing_domains.add(domain)

        new_domain = self._extract_domain(source_url)
        if new_domain in existing_domains and self.sources:
            return

        self.sources.append(source_data)
        self.unique_source_count = len(set(
            self._extract_domain(s.get("url", "")) for s in self.sources
        ))
        self.last_updated = datetime.now(timezone.utc)
        self._update_status()

    def _update_status(self) -> None:
        """Recalculate corroboration status based on source count and reputation."""
        if self.unique_source_count == 0:
            self.status = CorroborationStatus.UNVERIFIED
        elif self.unique_source_count == 1:
            self.status = CorroborationStatus.SINGLE_SOURCE
        elif self.unique_source_count == 2:
            self.status = CorroborationStatus.CROSS_REFERENCED
        else:
            self.status = CorroborationStatus.VERIFIED

        # Calculate weighted corroboration score
        total_tier_score = 0.0
        for s in self.sources:
            tier = classify_source_tier(s.get("source"))
            total_tier_score += TIER_SCORES[tier]

        self.corroboration_score = min(
            1.0,
            (total_tier_score / max(self.unique_source_count, 1)) *
            min(1.0, self.unique_source_count / 3.0)
        )

    @staticmethod
    def _extract_domain(url: str) -> str:
        """Extract domain from URL for source dedup."""
        if not url:
            return ""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            return parsed.netloc.lower().replace("www.", "")
        except Exception:
            return url.lower()

    def to_dict(self) -> dict:
        return {
            "story_key": self.story_key,
            "headline": self.headline,
            "summary": self.summary,
            "status": self.status.value,
            "corroboration_score": round(self.corroboration_score, 3),
            "unique_source_count": self.unique_source_count,
            "first_seen": self.first_seen.isoformat(),
            "last_updated": self.last_updated.isoformat(),
            "sources": self.sources[:10],
        }


class CrossCorroborationEngine:
    """
    Engine that groups related articles, cross-references them,
    and escalates threat levels only when multiple independent sources confirm.

    This is the core signal-vs-noise separator.
    """

    def __init__(self, min_sources_for_verification: int = 2):
        self.min_sources = min_sources_for_verification
        self.stories: Dict[str, CorroboratedStory] = {}
        self._cleanup_interval = 3600  # 1 hour
        self._last_cleanup = time.time()

    def process_article(self, article: dict) -> CorroboratedStory:
        """
        Ingest an article and cross-reference it against existing stories.
        Returns the CorroboratedStory it was matched to (or a new one).
        """
        self._maybe_cleanup()

        headline = article.get("title") or article.get("headline") or ""
        summary = article.get("summary") or article.get("content") or ""
        story_key = self._compute_story_key(headline, summary, article)

        if story_key in self.stories:
            story = self.stories[story_key]
            story.add_source(article)
            logger.debug(
                "[Corroboration] Story '%s' now has %d sources (status=%s, score=%.2f)",
                story_key[:32], story.unique_source_count,
                story.status.value, story.corroboration_score,
            )
        else:
            story = CorroboratedStory(story_key, headline, summary[:300])
            story.add_source(article)
            self.stories[story_key] = story
            logger.debug("[Corroboration] New story '%s' registered", story_key[:32])

        return story

    def get_verified_stories(self, min_score: float = 0.5) -> List[CorroboratedStory]:
        """Return stories that meet minimum corroboration score threshold."""
        return [
            s for s in self.stories.values()
            if s.corroboration_score >= min_score
            and s.status in (CorroborationStatus.CROSS_REFERENCED, CorroborationStatus.VERIFIED)
        ]

    def get_story_by_key(self, story_key: str) -> Optional[CorroboratedStory]:
        return self.stories.get(story_key)

    def _compute_story_key(self, headline: str, summary: str, article: dict) -> str:
        """
        Compute a story grouping key using headline similarity and key entities.
        Stories about the same event from different sources should map to the same key.
        """
        normalized = _normalize_text(headline)

        # Extract potential location/entity anchors
        entities = article.get("entities", [])
        if isinstance(entities, str):
            try:
                entities = json.loads(entities)
            except Exception:
                entities = []

        location = article.get("sector") or article.get("location_name") or ""
        country = article.get("country_code") or ""

        # Build composite key from first few words + country + location anchor
        words = normalized.split()
        key_words = [w for w in words if len(w) > 3][:5]
        key_components = key_words + [country.lower(), location.lower()]
        key_hash = hashlib.md5(
            " ".join(key_components).encode("utf-8")
        ).hexdigest()[:16]

        return key_hash

    def _maybe_cleanup(self) -> None:
        """Periodically remove old stories to prevent memory growth."""
        now = time.time()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now

        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        expired_keys = [
            key for key, story in self.stories.items()
            if story.last_updated < cutoff
        ]
        for key in expired_keys:
            del self.stories[key]

        if expired_keys:
            logger.info("[Corroboration] Cleaned up %d expired stories", len(expired_keys))


# ─── Threat Escalation Gate ──────────────────────────────────────────────────

class ThreatEscalationGate:
    """
    Gates threat level escalation using cross-corroboration.
    A threat level is only escalated if multiple independent sources confirm it.
    """

    THREAT_HIERARCHY = {"Low": 0, "Moderate": 1, "High": 2, "Critical": 3}

    def __init__(self, engine: CrossCorroborationEngine):
        self.engine = engine

    def should_escalate(
        self,
        proposed_level: str,
        story_key: str,
        current_level: str = "Low",
    ) -> Tuple[bool, str, float]:
        """
        Determine if a proposed threat level escalation should proceed.

        Returns: (should_escalate, effective_level, confidence)
        """
        story = self.engine.get_story_by_key(story_key)

        if not story:
            # No corroboration data - only allow escalation to Moderate
            if self.THREAT_HIERARCHY.get(proposed_level, 0) > self.THREAT_HIERARCHY.get("Moderate", 1):
                return False, current_level, 0.3
            return True, proposed_level, 0.5

        if story.status == CorroborationStatus.VERIFIED:
            # Multiple independent sources confirm - full escalation allowed
            return True, proposed_level, min(1.0, story.corroboration_score + 0.2)

        if story.status == CorroborationStatus.CROSS_REFERENCED:
            # 2 sources - allow up to High, cap Critical
            proposed_idx = self.THREAT_HIERARCHY.get(proposed_level, 0)
            max_level = self.THREAT_HIERARCHY["High"]
            effective_idx = min(proposed_idx, max_level)
            effective_level = [k for k, v in self.THREAT_HIERARCHY.items() if v == effective_idx][0]
            return effective_idx <= max_level, effective_level, story.corroboration_score

        # Single source or unverified - cap at Moderate
        proposed_idx = self.THREAT_HIERARCHY.get(proposed_level, 0)
        max_level = self.THREAT_HIERARCHY["Moderate"]
        effective_idx = min(proposed_idx, max_level)
        effective_level = [k for k, v in self.THREAT_HIERARCHY.items() if v == effective_idx][0]
        return True, effective_level, story.corroboration_score * 0.7


# ─── Entity Extraction (Lightweight) ─────────────────────────────────────────

KNOWN_ORGS = {
    "pla", "nato", "un", "united nations", "eu", "asean", "aukus",
    "ministry of defense", "ministry of foreign affairs", "pentagon",
    "cia", "fbi", "nsa", "isro", "drdo", "raw", "isi", "mossad",
}

KNOWN_WEAPONS = {
    "missile", "uav", "drone", "radar", "fighter", "frigate", "submarine",
    "destroyer", "tank", "artillery", "hypersonic", "s-400", "s-300",
    "patriot", "iron dome", "tomahawk", "brahmos", "agni", "j-20",
    "su-30mki", "rafale", "f-35", "f-22",
}

KNOWN_MILITARY_UNITS = {
    "army", "navy", "air force", "marines", "coast guard",
    "special forces", "command", "division", "regiment", "battalion",
    "theater command", "frontier force", "border guard",
}


def extract_entities_lightweight(text: str) -> dict:
    """
    Lightweight entity extraction without heavy NLP dependencies.
    Uses pattern matching for known entities.
    """
    if not text:
        return {"countries": [], "organizations": [], "weapons": [], "militaryUnits": [], "people": []}

    text_lower = text.lower()
    words = text.split()

    weapons_found = [w for w in KNOWN_WEAPONS if w in text_lower]
    orgs_found = [o for o in KNOWN_ORGS if o in text_lower]
    military_units = [u for u in KNOWN_MILITARY_UNITS if u in text_lower]

    # Simple capitalized name detection for people
    people = []
    for i, word in enumerate(words):
        if (word[0].isupper() and len(word) > 2 and
                i > 0 and words[i - 1][0].isupper() and
                word not in {"The", "This", "That", "When", "Where", "What", "How", "After", "Before"}):
            person = f"{words[i-1]} {word}"
            if len(person) < 40:
                people.append(person)

    return {
        "countries": [],
        "organizations": list(set(orgs_found)),
        "weapons": list(set(weapons_found)),
        "militaryUnits": list(set(military_units)),
        "people": list(set(people[:5])),
    }


# ─── Global Singleton ────────────────────────────────────────────────────────

corroboration_engine = CrossCorroborationEngine(min_sources_for_verification=2)
escalation_gate = ThreatEscalationGate(corroboration_engine)
