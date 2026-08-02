from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import settings
from backend.app.database import Article
from backend.app.observability import metrics

logger = logging.getLogger("drishya.classifier")


class MemoryLiveStream:
    def __init__(self) -> None:
        self.articles: List[dict] = []
        self.subscribers: List[Any] = []

    def publish(self, article_data: dict) -> None:
        self.articles.append(article_data)
        if len(self.articles) > 100:
            self.articles.pop(0)
        for sub in list(self.subscribers):
            try:
                sub(article_data)
            except Exception:
                continue

    def subscribe(self, callback: Any) -> None:
        self.subscribers.append(callback)


memory_stream = MemoryLiveStream()

_redis_dedup = None
_dedup_lock = None


def _get_dedup_lock():
    global _dedup_lock
    if _dedup_lock is None:
        import asyncio

        _dedup_lock = asyncio.Lock()
    return _dedup_lock


async def _get_redis():
    global _redis_dedup
    if _redis_dedup is not None:
        return _redis_dedup
    if not settings.enable_redis_dedup:
        _redis_dedup = False
        return _redis_dedup

    async with _get_dedup_lock():
        if _redis_dedup is not None:
            return _redis_dedup
        try:
            _redis_dedup = aioredis.from_url(settings.redis_url, decode_responses=True)
            await _redis_dedup.ping()
        except Exception as exc:
            logger.warning("[Classifier] Redis unavailable for deduplication: %s", exc)
            _redis_dedup = False
    return _redis_dedup


async def _bump_archive_version() -> None:
    redis_conn = await _get_redis()
    if not redis_conn:
        return
    try:
        await redis_conn.incr("drishya:archive:version")
    except Exception:
        return


class ImpactClassifier:
    def __init__(self) -> None:
        self.impact_labels = ["High Impact", "Medium Impact", "Normal Impact"]
        self.dept_labels = [
            "Military & Defense",
            "Economic & Financial",
            "Social Affairs & Welfare",
            "Political & Diplomatic",
        ]
        self.label_keywords = {
            "High Impact": r"\b(troop|deployment|missile|clash|invasion|drill|sanction|nuclear|navy|air force|border conflict|skirmish|casualty|coup|strike)s?\b",
            "Medium Impact": r"\b(bilateral|agreement|trade deal|tariff|summit|protest|refugee|inflation|corruption|embassy|drone|port|aid)s?\b",
            "Normal Impact": r"\b(quiz|sport|cricket|entertainment|weather|stock price|tourism|festival|culture|feature)s?\b",
        }
        self.dept_keywords = {
            "Military & Defense": r"\b(pla|loc|lac|military|troop|air force|navy|missile|radar|defense|border post|uav|drone|arms|exercise|drill|clash|patrol)s?\b",
            "Economic & Financial": r"\b(economic|trade|finance|tariff|port|investment|infrastructure|road|highway|corridor|inflation|currency|gdp|aid)s?\b",
            "Social Affairs & Welfare": r"\b(social|refugee|community|migration|protest|settlement|civilian|health|disease|aid|disaster|religion|citizenship)s?\b",
            "Political & Diplomatic": r"\b(political|diplomat|embassy|border crossing|government|summit|treaty|talks|meeting|minister|president|signing)s?\b",
        }

    @staticmethod
    def _text(article_data: Dict[str, Any]) -> str:
        return f"{article_data.get('title', '')} {article_data.get('headline', '')} {article_data.get('content', '')}".lower()

    @staticmethod
    def _preview(article_data: Dict[str, Any], size: int = 280) -> str:
        content = article_data.get("content") or ""
        preview = re.sub(r"\s+", " ", content[:size]).strip()
        return preview.lower()

    async def is_duplicate(self, article_data: Dict[str, Any]) -> bool:
        fingerprint = hashlib.sha256(
            f"{article_data.get('url','')}|{self._preview(article_data)}".encode("utf-8")
        ).hexdigest()
        redis_conn = await _get_redis()
        if not redis_conn:
            return False
        if await redis_conn.set(f"drishya:dedup:{fingerprint}", "1", ex=24 * 60 * 60, nx=True):
            return False
        return True

    def classify_shared(self, title: str, content: str) -> Tuple[str, str]:
        text = f"{title} {content}".lower()
        scores = Counter()

        for label, pattern in self.label_keywords.items():
            scores[label] += len(re.findall(pattern, text))

        dept_scores = Counter()
        for label, pattern in self.dept_keywords.items():
            dept_scores[label] += len(re.findall(pattern, text))

        # Relaxed classification for abundant high-fidelity operational signals
        if scores["High Impact"] >= 1:
            impact = "High Impact"
        elif scores["Medium Impact"] > 0 or scores["Normal Impact"] == 0:
            impact = "Medium Impact"
        else:
            impact = "Normal Impact"

        dept = dept_scores.most_common(1)[0][0] if dept_scores else "Political & Diplomatic"
        return impact, dept

    def classify(self, title: str, content: str) -> Tuple[str, str]:
        # Single shared pass over the article text for both labels.
        return self.classify_shared(title, content[:1200])

    async def route_article(self, article_data: Dict[str, Any]) -> Tuple[str, str]:
        impact, dept = self.classify(article_data.get("title", ""), article_data.get("content", ""))
        article_data["impact_level"] = impact
        article_data["department"] = dept
        return impact, dept

    async def _publish_realtime(self, payload: Dict[str, Any]) -> None:
        memory_stream.publish(payload)
        redis_conn = await _get_redis()
        if not redis_conn:
            return
        try:
            await redis_conn.publish("live_stream", json.dumps(payload))
        except Exception as exc:
            logger.debug("[Classifier] Live stream publish skipped: %s", exc)

    async def save_article(self, db: AsyncSession, article_data: Dict[str, Any], embedding: Optional[List[float]] = None) -> bool:
        return (await self.persist_high_impact_batch(db, [article_data], embeddings=[embedding] if embedding else None)) > 0

    async def persist_high_impact_batch(
        self,
        db: AsyncSession,
        articles: List[Dict[str, Any]],
        embeddings: Optional[List[Optional[List[float]]]] = None,
    ) -> int:
        inserted = 0
        rows: List[Article] = []
        for idx, article_data in enumerate(articles):
            impact, dept = await self.route_article(article_data)
            
            # Send real-time updates for all ingested articles
            await self._publish_realtime(
                {
                    "title": article_data["title"],
                    "headline": article_data.get("headline") or article_data["title"],
                    "summary": article_data.get("summary") or article_data["title"],
                    "content": article_data["content"],
                    "url": article_data["url"],
                    "source": article_data.get("source"),
                    "country_code": article_data["country_code"],
                    "published_at": article_data["published_at"].isoformat() if isinstance(article_data["published_at"], datetime) else str(article_data["published_at"]),
                    "impact_level": impact,
                    "department": dept,
                }
            )

            rows.append(
                Article(
                    title=article_data["title"],
                    headline=article_data.get("headline") or article_data["title"],
                    summary=article_data.get("summary"),
                    content=article_data["content"],
                    url=article_data["url"],
                    source=article_data.get("source"),
                    country_code=article_data["country_code"],
                    published_at=article_data["published_at"],
                    impact_level=impact,
                    department=dept,
                    embedding=(embeddings[idx] if embeddings and idx < len(embeddings) else None),
                )
            )

        if rows:
            # Remove any records that already exist in the database by URL to avoid batch integrity errors.
            urls = [row.url for row in rows if row.url]
            if urls:
                try:
                    existing_result = await db.execute(select(Article.url).where(Article.url.in_(urls)))
                    existing_urls = {row[0] for row in existing_result.all()}
                except Exception:
                    existing_urls = set()
            else:
                existing_urls = set()

            rows = [row for row in rows if row.url and row.url not in existing_urls]

            if rows:
                try:
                    db.add_all(rows)
                    await db.commit()
                    inserted = len(rows)
                    metrics.state.classification_batches_total += 1
                    await _bump_archive_version()
                    logger.info("[Classifier] Batch saved %s high-impact articles.", inserted)
                except Exception as exc:
                    await db.rollback()
                    logger.warning("[Classifier] Batch insert skipped or partially failed: %s", exc)
                    inserted = 0
            else:
                logger.debug("[Classifier] No new high-impact articles to save after deduplication.")

        return inserted


async def classify_and_store_batch(
    db: AsyncSession,
    articles: List[Dict[str, Any]],
    embeddings: Optional[List[Optional[List[float]]]] = None,
) -> Dict[str, int]:
    classifier = ImpactClassifier()
    high_impact = 0
    streamed = 0
    skipped_duplicates = 0
    unique_articles: List[Dict[str, Any]] = []
    unique_embeddings: List[Optional[List[float]]] = []

    for index, article in enumerate(articles):
        if await classifier.is_duplicate(article):
            skipped_duplicates += 1
            metrics.state.ingestion_duplicates_total += 1
            continue
        unique_articles.append(article)
        if embeddings:
            unique_embeddings.append(embeddings[index] if index < len(embeddings) else None)

    high_impact = await classifier.persist_high_impact_batch(
        db,
        unique_articles,
        embeddings=unique_embeddings if embeddings else None,
    )
    streamed = len(unique_articles) - high_impact
    metrics.state.ingestion_articles_total += len(unique_articles)
    return {
        "processed": len(unique_articles),
        "high_impact": high_impact,
        "streamed": streamed,
        "duplicates": skipped_duplicates,
    }
