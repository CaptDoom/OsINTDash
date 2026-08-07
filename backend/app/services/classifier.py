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


def compute_source_reputation(source: Optional[str]) -> str:
    if not source:
        return "Unrated"
    src = source.lower().strip()
    
    # Wire agencies and major outlets
    verified = [
        "reuters.com", "apnews.com", "aljazeera.com", "bbc.com", "dw.com",
        "france24.com", "theguardian.com", "nytimes.com", "bloomberg.com",
        "reuters", "apnews", "aljazeera", "bbc", "dw", "france24", "theguardian",
        "nytimes", "bloomberg", "reuters (seeded)", "bbc.com (demo)"
    ]
    for v in verified:
        if v in src:
            return "Verified Source"
            
    # Known aggregators
    aggregators = [
        "yahoo.com", "msn.com", "google.com", "news.google.com", "reddit.com",
        "feedburner", "rss", "aggregator"
    ]
    for a in aggregators:
        if a in src:
            return "Developing"
            
    # Unknown/new domains
    return "Unverified"


_transformer = None
def get_transformer():
    global _transformer
    if _transformer is None:
        from sentence_transformers import SentenceTransformer
        _transformer = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')
    return _transformer

DEPT_CENTROIDS = {
    "Military & Defense": [
        "military troop deployment navy combat missile attack army military base exercise troops air force soldiers carrier strike weapons border clash",
        "clashes skirmish casualties gunfire shelling troop movements artillery defense system defense ministry jets fighters drone strike war conflict"
    ],
    "Economic & Financial": [
        "economic growth trade agreement inflation gdp tariffs interest rates trade deal financial market stocks bonds central bank investments business port",
        "currency devaluation fiscal policy economic cooperation imports exports industrial production supply chain trade deficit commerce recession budget"
    ],
    "Social Affairs & Welfare": [
        "humanitarian aid refugee relief migration human rights protests civil unrest social welfare healthcare public education community displacement",
        "disaster response epidemic disease outbreaks labor union strike citizen rights religious freedom housing food security social assistance census"
    ],
    "Political & Diplomatic": [
        "diplomatic relations bilateral summit ambassador treaty signing geopolitical talks state visit embassy opening administration foreign policy minister",
        "elections political parties parliament legislation policy debate government formation coalition leadership transition constitutional reform diplomatic protest"
    ],
    "Technology & Cyber": [
        "cyberattack ransomware malware hacking computer networks database breach artificial intelligence machine learning semiconductor chips technology innovation",
        "telecom 5g network fiber optics digital surveillance encryption data privacy software system cloud computing cyber espionage high-tech hardware drone tech"
    ]
}

IMPACT_CENTROIDS = {
    "High Impact": [
        "war conflict invasion troop deployment casualties missiles nuclear attack defense mobilization border clash air strike declaration of war emergency coup martial law security threat navy fleet airspace violation tactical",
        "extreme security threat defense operations military escalation weapons nuclear capabilities troops combat military drills critical border standoff aircraft interception submarine"
    ],
    "Medium Impact": [
        "trade deals bilateral summits summits talks trade tariff cooperation agreements embassy protest political meeting state visit political reform ministers election cabinet change",
        "foreign relations cooperation agreement international summit policy reform border crossing trade partnership investment projects infrastructure diplomatic talks"
    ],
    "Normal Impact": [
        "weather forecast sports tournament entertainment celebrities movie reviews stock price changes cultural festivals travel guide tourism museum opening local news daily routines consumer product releases features games",
        "daily weather forecast domestic league cricket football quiz show music release food recipe lifestyle tips holiday destinations science trivia tech gadget review"
    ]
}


class ImpactClassifier:
    def __init__(self) -> None:
        self.impact_labels = ["High Impact", "Medium Impact", "Normal Impact"]
        self.dept_labels = [
            "Military & Defense",
            "Economic & Financial",
            "Social Affairs & Welfare",
            "Political & Diplomatic",
            "Technology & Cyber",
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
            "Technology & Cyber": r"\b(cyber|ransomware|malware|hacker|cyberattack|semiconductor|chip|ai|artificial intelligence|robotics|quantum|satellite|surveillance)s?\b"
        }
        self._dept_centroids = None
        self._impact_centroids = None

    def get_centroids(self):
        if self._dept_centroids is None:
            import numpy as np
            transformer = get_transformer()
            self._dept_centroids = {}
            for dept, exemplars in DEPT_CENTROIDS.items():
                vectors = transformer.encode(exemplars, convert_to_numpy=True)
                mean_vector = vectors.mean(axis=0)
                norm = np.linalg.norm(mean_vector)
                self._dept_centroids[dept] = mean_vector / norm if norm > 0 else mean_vector
        return self._dept_centroids

    def get_impact_centroids(self):
        if self._impact_centroids is None:
            import numpy as np
            transformer = get_transformer()
            self._impact_centroids = {}
            for imp, exemplars in IMPACT_CENTROIDS.items():
                vectors = transformer.encode(exemplars, convert_to_numpy=True)
                mean_vector = vectors.mean(axis=0)
                norm = np.linalg.norm(mean_vector)
                self._impact_centroids[imp] = mean_vector / norm if norm > 0 else mean_vector
        return self._impact_centroids

    def classify_regex(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        scores = Counter()
        for label, pattern in self.label_keywords.items():
            scores[label] += len(re.findall(pattern, text))

        dept_scores = Counter()
        for label, pattern in self.dept_keywords.items():
            dept_scores[label] += len(re.findall(pattern, text))

        impact = None
        if scores["High Impact"] >= 1:
            impact = "High Impact"
        elif scores["Medium Impact"] > 0:
            impact = "Medium Impact"
        elif scores["Normal Impact"] > 0:
            impact = "Normal Impact"

        dept = dept_scores.most_common(1)[0][0] if dept_scores and dept_scores.most_common(1)[0][1] > 0 else None
        return impact, dept

    def classify_centroid(self, text: str) -> Tuple[str, str]:
        import numpy as np
        centroids = self.get_centroids()
        transformer = get_transformer()
        text_vec = transformer.encode(text, convert_to_numpy=True)
        text_norm = np.linalg.norm(text_vec)
        if text_norm > 0:
            text_vec = text_vec / text_norm
        else:
            return "Normal Impact", "Political & Diplomatic"

        best_dept = "Political & Diplomatic"
        best_dept_score = -1.0
        for dept, centroid in centroids.items():
            score = float(np.dot(text_vec, centroid))
            if score > best_dept_score:
                best_dept_score = score
                best_dept = dept

        impact_centroids = self.get_impact_centroids()
        best_impact = "Normal Impact"
        best_impact_score = -1.0
        for imp, centroid in impact_centroids.items():
            score = float(np.dot(text_vec, centroid))
            if score > best_impact_score:
                best_impact_score = score
                best_impact = imp

        return best_impact, best_dept

    async def classify_llm_fallback(self, title: str, content: str) -> Tuple[str, str]:
        metrics.state.classification_llm_fallback_total += 1
        
        prompt = f"""
        Analyze this article and classify it.
        Categories: "Military & Defense", "Economic & Financial", "Social Affairs & Welfare", "Political & Diplomatic", "Technology & Cyber".
        Impact: "High Impact", "Medium Impact", "Normal Impact".
        
        Return JSON format with keys "impact" and "department". Example:
        {{"impact": "High Impact", "department": "Military & Defense"}}
        
        ARTICLE TITLE: {title}
        ARTICLE CONTENT: {content[:1000]}
        """
        
        summary = ""
        if settings.llm_provider == "ollama" and settings.ollama_base_url:
            try:
                from backend.app.services.summarizer import call_ollama
                summary = await call_ollama(prompt, "You are a classification assistant.")
            except Exception:
                pass
        if not summary and settings.openai_api_key:
            try:
                from backend.app.services.summarizer import call_openai
                summary = await call_openai(prompt, "You are a classification assistant.")
            except Exception:
                pass
        if not summary and settings.google_api_key:
            try:
                from backend.app.services.summarizer import call_gemini
                summary = await call_gemini(prompt, "You are a classification assistant.")
            except Exception:
                pass
                
        if summary:
            try:
                import json
                match = re.search(r"\{.*?\}", summary, re.DOTALL)
                if match:
                    parsed = json.loads(match.group(0))
                    imp = parsed.get("impact")
                    dept = parsed.get("department")
                    if imp in self.impact_labels and dept in self.dept_labels:
                        return imp, dept
            except Exception as e:
                logger.warning(f"Failed to parse LLM classification: {e}")
                
        metrics.state.classification_regex_fallback_total += 1
        return self.classify_centroid(f"{title} {content}")

    def classify(self, title: str, content: str) -> Tuple[str, str]:
        text = f"{title} {content[:1200]}".lower()
        imp, dept = self.classify_regex(text)
        if not imp or not dept:
            imp_sem, dept_sem = self.classify_centroid(text)
            imp = imp or imp_sem
            dept = dept or dept_sem
        return imp, dept

    async def route_article(self, article_data: Dict[str, Any]) -> Tuple[str, str]:
        title = article_data.get("title", "")
        content = article_data.get("content", "")
        impact, dept = self.classify(title, content)
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
        if not embeddings:
            try:
                transformer = get_transformer()
                texts = [f"{art['title']} {art.get('summary', '') or art['content'][:300]}" for art in articles]
                vectors = transformer.encode(texts, convert_to_numpy=True).tolist()
                embeddings = [vec for vec in vectors]
            except Exception as e:
                logger.error(f"[Classifier] Failed to compute batch embeddings: {e}")
                embeddings = [None] * len(articles)
                
        inserted = 0
        rows: List[Article] = []
        for idx, article_data in enumerate(articles):
            impact, dept = await self.route_article(article_data)
            
            source = article_data.get("source")
            reputation = compute_source_reputation(source)
            confidence = article_data.get("confidence_score") or 0.98
            cand_embedding = embeddings[idx] if embeddings and idx < len(embeddings) else None

            # Check near-duplicates inside same country bucket
            near_dup_found = False
            if cand_embedding:
                try:
                    import numpy as np
                    from sqlalchemy import select
                    country_code = article_data["country_code"]
                    
                    stmt_ex = select(Article).where(Article.country_code == country_code)
                    res_ex = await db.execute(stmt_ex)
                    existing_articles = res_ex.scalars().all()
                    
                    cand_vec = np.array(cand_embedding, dtype=np.float32)
                    cand_norm = np.linalg.norm(cand_vec)
                    if cand_norm > 0:
                        cand_vec_norm = cand_vec / cand_norm
                        for existing_art in existing_articles:
                            ex_vec_val = existing_art.embedding
                            if not ex_vec_val:
                                continue
                                
                            if isinstance(ex_vec_val, str):
                                try:
                                    import json
                                    ex_vec_list = json.loads(ex_vec_val)
                                except Exception:
                                    continue
                            elif isinstance(ex_vec_val, (list, tuple)):
                                ex_vec_list = ex_vec_val
                            else:
                                try:
                                    ex_vec_list = list(ex_vec_val)
                                except Exception:
                                    continue
                                    
                            if len(ex_vec_list) != len(cand_vec):
                                continue
                                
                            ex_vec = np.array(ex_vec_list, dtype=np.float32)
                            ex_norm = np.linalg.norm(ex_vec)
                            if ex_norm <= 0:
                                continue
                                
                            ex_vec_norm = ex_vec / ex_norm
                            similarity = float(np.dot(cand_vec_norm, ex_vec_norm))
                            if similarity > 0.92:
                                near_dup_found = True
                                current_conf = existing_art.confidence_score or 0.98
                                existing_art.confidence_score = min(1.00, current_conf + 0.05)
                                db.add(existing_art)
                                metrics.state.dedup_near_duplicate_dropped_total += 1
                                logger.info(f"[Classifier] Near-duplicate dropped. URL: {article_data['url']}. Boosted existing confidence.")
                                break
                except Exception as ex_err:
                    logger.error(f"[Classifier] Near-duplicate check error: {ex_err}")
                    
            if near_dup_found:
                continue

            # Send real-time updates for all ingested articles
            await self._publish_realtime(
                {
                    "title": article_data["title"],
                    "headline": article_data.get("headline") or article_data["title"],
                    "summary": article_data.get("summary") or article_data["title"],
                    "content": article_data["content"],
                    "url": article_data["url"],
                    "source": source,
                    "country_code": article_data["country_code"],
                    "published_at": article_data["published_at"].isoformat() if isinstance(article_data["published_at"], datetime) else str(article_data["published_at"]),
                    "impact_level": impact,
                    "department": dept,
                    "source_reputation": reputation,
                    "confidence_score": confidence,
                }
            )

            summary_text = article_data.get("summary")
            if not summary_text or not summary_text.strip():
                content_text = article_data.get("content") or ""
                summary_text = content_text.strip()[:180] + "..." if len(content_text.strip()) > 180 else content_text.strip()
            if not summary_text:
                summary_text = "Tactical intelligence briefing restricted."

            rows.append(
                Article(
                    title=article_data["title"],
                    headline=article_data.get("headline") or article_data["title"],
                    summary=summary_text,
                    content=article_data["content"],
                    url=article_data["url"],
                    source=source,
                    country_code=article_data["country_code"],
                    published_at=article_data["published_at"],
                    impact_level=impact,
                    department=dept,
                    embedding=(cand_embedding if cand_embedding else None),
                    source_reputation=reputation,
                    confidence_score=confidence,
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
