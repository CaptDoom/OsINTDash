import os
import logging
import re
import json
from datetime import datetime
import redis.asyncio as aioredis
from typing import Dict, Any, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.config import settings
from backend.app.database import Article

logger = logging.getLogger("drishya.classifier")

# Simple memory fallback for live streamed articles in case Redis is offline
class MemoryLiveStream:
    def __init__(self):
        self.articles = []
        self.subscribers = []

    def publish(self, article_data: dict):
        self.articles.append(article_data)
        if len(self.articles) > 100:
            self.articles.pop(0) # Keep last 100
        # Notify subscribers (WebSocket connections)
        for sub in self.subscribers:
            try:
                sub(article_data)
            except Exception:
                pass

    def subscribe(self, callback):
        self.subscribers.append(callback)

memory_stream = MemoryLiveStream()

# Load zero-shot classifier pipelines lazily
_classifier_pipeline = None

def get_classifier():
    global _classifier_pipeline
    if _classifier_pipeline is not None:
        return _classifier_pipeline
    
    use_bart = os.getenv("USE_BART_CLASSIFIER", "false").lower() == "true"
    if not use_bart:
        logger.info("[Classifier] USE_BART_CLASSIFIER is disabled or not set to true. Defaulting to fast keyword heuristics.")
        _classifier_pipeline = "fallback"
        return _classifier_pipeline

    try:
        from transformers import pipeline
        logger.info("[Classifier] Loading facebook/bart-large-mnli zero-shot classification pipeline...")
        _classifier_pipeline = pipeline("zero-shot-classification", model="facebook/bart-large-mnli", device=-1)
        logger.info("[Classifier] BART model loaded successfully.")
    except Exception as e:
        logger.warning(f"[Classifier] Failed to load transformers model: {e}. Falling back to keyword heuristics.")
        _classifier_pipeline = "fallback"
    return _classifier_pipeline

class ImpactClassifier:
    def __init__(self):
        self.impact_labels = ["High Impact", "Medium Impact", "Normal Impact"]
        self.dept_labels = [
            "Military & Defense", 
            "Economic & Financial", 
            "Social Affairs & Welfare", 
            "Political & Diplomatic"
        ]
        
        # Fallback keyword maps
        self.impact_keywords = {
            "High Impact": r"(troop|deployment|missile|clash|invasion|drills|sanctions|nuclear|navy|air force|border conflict|skirmish|casualty|veto|coup)",
            "Medium Impact": r"(bilateral|agreement|trade deal|tariff|summit|protest|refugee|inflation|corruption|embassy|drone output)",
            "Normal Impact": r"(quiz|sport|cricket|entertainment|weather|stock price|tourism|festival|cultural)"
        }
        
        self.dept_keywords = {
            "Military & Defense": r"(pla|loc|lac|military|troop|air force|navy|missile|radar|defense|border post|uav|drone|arms|exercise|drill|clash|patrol)",
            "Economic & Financial": r"(economic|trade|finance|tariff|port|investment|infrastructure|road|highway|corridor|inflation|currency|gdp|aid)",
            "Social Affairs & Welfare": r"(social|refugee|community|migration|protest|settlement|civilian|health|disease|aid|disaster|religion|citizenship)",
            "Political & Diplomatic": r"(political|diplomat|embassy|border crossing|government|summit|treaty|talks|meeting|minister|president|signing)"
        }

    def classify_heuristics(self, title: str, content: str) -> Tuple[str, str]:
        text = f"{title} {content}".lower()
        
        # 1. Determine Department
        dept = "Political & Diplomatic" # Default
        max_matches = 0
        for label, pattern in self.dept_keywords.items():
            matches = len(re.findall(pattern, text))
            if matches > max_matches:
                max_matches = matches
                dept = label
                
        # 2. Determine Impact Level
        impact = "Normal Impact" # Default
        if re.search(self.impact_keywords["High Impact"], text):
            impact = "High Impact"
        elif re.search(self.impact_keywords["Medium Impact"], text):
            impact = "Medium Impact"
            
        return impact, dept

    def classify(self, title: str, content: str) -> Tuple[str, str]:
        pipeline = get_classifier()
        text = f"{title}\n{content[:400]}"
        
        if pipeline == "fallback" or pipeline is None:
            return self.classify_heuristics(title, content)
            
        try:
            # Classify Impact
            res_impact = pipeline(text, self.impact_labels, multi_label=False)
            impact = res_impact["labels"][0]
            
            # Classify Department
            res_dept = pipeline(text, self.dept_labels, multi_label=False)
            dept = res_dept["labels"][0]
            
            return impact, dept
        except Exception as e:
            logger.warning(f"[Classifier] BART inference failed: {e}. Using heuristics fallback.")
            return self.classify_heuristics(title, content)

    async def save_article(self, db: AsyncSession, article_data: Dict[str, Any], embedding: Optional[List[float]] = None) -> bool:
        """
        CRITICAL BUSINESS RULE persistence function:
        - If impact_level is 'High Impact', save to PostgreSQL high_impact_articles table.
        - If impact_level is 'Medium Impact' or 'Normal Impact', publish to Redis live stream, bypassing database storage.
        """
        impact, dept = self.classify(article_data["title"], article_data["content"])
        article_data["impact_level"] = impact
        article_data["department"] = dept
        
        # Prepare payload
        payload = {
            "title": article_data["title"],
            "headline": article_data["headline"],
            "summary": article_data.get("summary") or article_data["title"],
            "content": article_data["content"],
            "url": article_data["url"],
            "source": article_data.get("source"),
            "country_code": article_data["country_code"],
            "published_at": article_data["published_at"].isoformat() if isinstance(article_data["published_at"], datetime) else str(article_data["published_at"]),
            "impact_level": impact,
            "department": dept
        }

        if impact == "High Impact":
            try:
                # Store in relational database (PostgreSQL/SQLite)
                db_article = Article(
                    title=article_data["title"],
                    headline=article_data["headline"],
                    summary=article_data.get("summary"),
                    content=article_data["content"],
                    url=article_data["url"],
                    source=article_data.get("source"),
                    country_code=article_data["country_code"],
                    published_at=article_data["published_at"],
                    impact_level=impact,
                    department=dept,
                    embedding=embedding
                )
                db.add(db_article)
                await db.commit()
                logger.info(f"[Classifier] Saved 'High Impact' article to PostgreSQL: {article_data['title'][:40]}")
                return True
            except Exception as e:
                # Handle unique URL constraint violation gracefully
                await db.rollback()
                logger.warning(f"[Classifier] Database insert skipped (likely duplicate URL): {e}")
                return False
        else:
            # Stream to Redis / Memory pub-sub
            logger.info(f"[Classifier] Ingested '{impact}' article. Streaming to Live UI.")
            memory_stream.publish(payload)
            
            # Try streaming to Redis if configured
            try:
                redis_conn = aioredis.from_url(settings.REDIS_URL, socket_timeout=2.0)
                await redis_conn.publish("live_stream", json.dumps(payload))
                await redis_conn.close()
            except Exception as e:
                # Silent failure if Redis is offline
                pass
            return False
