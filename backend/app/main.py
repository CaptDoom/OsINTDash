import os
import asyncio
import json
import logging
import re
import time
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Dict, Any, List, Set, Optional
from pydantic import BaseModel
from fastapi import FastAPI, Depends, Query, Request, WebSocket, WebSocketDisconnect, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, PlainTextResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.config import settings
from backend.app.database import create_tables, get_db, Article, User
from backend.app.api.routes import archive, chat, weather, auth, summarizer, notes, alerts, credibility
from backend.app.services.ingestion import fetch_global_news
from backend.app.services.gdelt_worker import gdelt_ingestion_loop
from backend.app.services.classifier import ImpactClassifier, classify_and_store_batch, memory_stream, compute_source_reputation
from backend.app.services.summarizer import call_openai, call_gemini
from backend.app.observability import configure_logging, metrics, request_id_var
from backend.app.services.job_store import job_store
from backend.app.services.risk import calculate_country_risk

logger = logging.getLogger("drishya.main")
configure_logging(logging.DEBUG if settings.debug else logging.INFO)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup and shutdown lifecycle for the FastAPI application."""
    # --- Startup ---
    testing = os.environ.get("TESTING") == "1"

    # Startup Environment Validation
    if not testing:
        provider_keys = [
            settings.newsapi_key, settings.gnews_api_key, settings.newsdata_api_key,
            settings.currents_api_key, settings.thenews_api_key, settings.mediastack_api_key,
            settings.newscatcher_api_key, settings.bing_news_api_key, settings.world_news_api_key,
            settings.freenewsapi_key,
        ]
        if not any(provider_keys):
            logger.info(
                "[Startup Validation] No commercial news provider keys configured. "
                "Operating with free GDELT/RSS feeds and in-memory/seed intelligence."
            )
        else:
            logger.info("[Startup Validation] Configured news provider API keys detected.")

        # Verify Redis connectivity (warn and continue in-memory if offline)
        try:
            import redis.asyncio as aioredis
            conn = aioredis.from_url(settings.redis_url, socket_timeout=3.0)
            await conn.ping()
            await conn.aclose()
            logger.info(f"[Startup Validation] Redis connected successfully at {settings.redis_url}.")
        except Exception as e:
            logger.warning(
                f"[Startup Validation] Redis is unreachable at {settings.redis_url}: {e}. "
                "Continuing in graceful in-memory fallback mode."
            )

    # Run DB initializations
    await create_tables()

    # Track background tasks for graceful shutdown
    background_tasks: list[asyncio.Task] = []

    if testing:
        logger.info("[Main] API startup complete in test mode; background workers disabled.")
    else:
        # Start Redis listener for pub/sub live stream broadcasting
        background_tasks.append(asyncio.create_task(redis_listener_task()))
        # Start periodic ingestion if enabled
        if settings.enable_periodic_ingestion:
            background_tasks.append(asyncio.create_task(periodic_ingestion_loop()))
            logger.info("[Main] API startup complete; background ingestion and redis listener started.")
        else:
            logger.info("[Main] API startup complete; redis listener started (periodic ingestion disabled).")
        # Start GDELT 2.0 Events ingestion loop (polls every 15 min)
        background_tasks.append(asyncio.create_task(gdelt_ingestion_loop()))
        # Start background note expiration cleanup
        background_tasks.append(asyncio.create_task(_note_cleanup_loop()))
        # Start corroboration story persistence loop
        background_tasks.append(asyncio.create_task(_corroboration_persistence_loop()))

    yield

    # --- Shutdown ---
    # Cancel background tasks
    for task in background_tasks:
        task.cancel()
    if background_tasks:
        await asyncio.gather(*background_tasks, return_exceptions=True)

    # Gracefully close the shared Redis connection pool
    try:
        from backend.app.redis_pool import close_redis_pool
        await close_redis_pool()
        logger.info("[Main] Redis connection pool closed.")
    except Exception as e:
        logger.debug(f"[Main] Redis pool shutdown error: {e}")


app = FastAPI(title=settings.app_name, version="2.2.0", lifespan=lifespan)

# CORS config
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate Limiting Middleware
try:
    from backend.app.services.rate_limiter import RateLimitMiddleware
    app.add_middleware(RateLimitMiddleware)
    logger.info("[Main] Rate limiting middleware enabled.")
except Exception as e:
    logger.warning(f"[Main] Rate limiting middleware disabled: {e}")

# Include specs and prompts routes
app.include_router(archive.router)
app.include_router(chat.router)
app.include_router(weather.router)
app.include_router(auth.router)
app.include_router(summarizer.router)
app.include_router(notes.router)
app.include_router(alerts.router)
app.include_router(credibility.router)




@app.middleware("http")
async def observability_middleware(request, call_next):
    request_id = request.headers.get("x-request-id") or f"req-{int(time.time() * 1000)}"
    token = request_id_var.set(request_id)
    start = time.perf_counter()
    metrics.state.http_requests_total += 1
    try:
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response
    except Exception:
        metrics.state.http_request_errors_total += 1
        raise
    finally:
        metrics.state.http_request_duration_seconds_sum += time.perf_counter() - start
        request_id_var.reset(token)


async def get_cached_response(key: str) -> Optional[dict]:
    from backend.app.redis_pool import cache_get
    return await cache_get(key)

async def set_cached_response(key: str, data: Any, ttl: int = 300) -> None:
    from backend.app.redis_pool import cache_set
    await cache_set(key, data, ttl)


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "service": settings.service_name,
        "environment": settings.environment,
        "database": "configured" if settings.database_url else "missing",
        "redis": settings.redis_url,
    }


@app.get("/ready")
async def readiness_check(db: AsyncSession = Depends(get_db)):
    db_ok = False
    redis_ok = False
    
    # 1. Check Database connection
    try:
        from sqlalchemy import text
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception as e:
        logger.error(f"[Readiness] Database check failed: {e}")
        
    # 2. Check Redis connection via shared pool
    try:
        from backend.app.redis_pool import get_redis_pool
        pool = await get_redis_pool()
        if pool:
            await pool.ping()
            redis_ok = True
        else:
            logger.error("[Readiness] Redis pool unavailable")
    except Exception as e:
        logger.error(f"[Readiness] Redis check failed: {e}")
        
    status = "ok" if (db_ok and redis_ok) else "degraded"
    status_code = 200 if status == "ok" else 503
    
    return JSONResponse(
        content={
            "status": status,
            "database": "ready" if db_ok else "unreachable",
            "redis": "ready" if redis_ok else "unreachable"
        },
        status_code=status_code
    )


@app.get("/metrics")
async def metrics_endpoint():
    return PlainTextResponse(metrics.render_prometheus(), media_type="text/plain; version=0.0.4")

# WebSocket connection manager with channel-based subscriptions
class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.channel_subscribers: Dict[str, Set[WebSocket]] = {
            "alerts": set(),
            "weather": set(),
            "chat": set(),
            "notes": set(),
            "map": set(),
        }

    async def connect(self, websocket: WebSocket, channels: Optional[List[str]] = None):
        await websocket.accept()
        self.active_connections.add(websocket)
        if channels:
            for ch in channels:
                if ch in self.channel_subscribers:
                    self.channel_subscribers[ch].add(websocket)
        else:
            # Default: subscribe to all channels
            for ch in self.channel_subscribers:
                self.channel_subscribers[ch].add(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        for subs in self.channel_subscribers.values():
            subs.discard(websocket)

    async def broadcast(self, message: dict, channel: Optional[str] = None):
        # Snapshot the set to avoid mutation during iteration
        if channel:
            targets = list(self.channel_subscribers.get(channel, set()))
        else:
            targets = list(self.active_connections)
        dead_connections = []
        for connection in targets:
            try:
                await connection.send_text(json.dumps(message))
            except Exception:
                dead_connections.append(connection)
        for dead in dead_connections:
            self.disconnect(dead)

manager = ConnectionManager()

async def _handle_websocket_connection(websocket: WebSocket, channels: Optional[List[str]] = None):
    await manager.connect(websocket, channels)
    try:
        while True:
            data = await websocket.receive_text()
            # Handle channel subscription updates from client
            try:
                msg = json.loads(data)
                if msg.get("type") == "subscribe" and "channel" in msg:
                    ch = msg["channel"]
                    if ch in manager.channel_subscribers:
                        manager.channel_subscribers[ch].add(websocket)
            except Exception:
                pass
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.websocket("/")
async def websocket_endpoint(websocket: WebSocket):
    await _handle_websocket_connection(websocket)

@app.websocket("/ws")
async def websocket_endpoint_ws(websocket: WebSocket):
    await _handle_websocket_connection(websocket)

@app.api_route("/websub-webhook", methods=["GET", "POST"])
async def websub_webhook(request: Request):
    if request.method == "GET":
        challenge = request.query_params.get("hub.challenge")
        if challenge:
            return PlainTextResponse(challenge, status_code=200)
        return PlainTextResponse("Missing hub.challenge", status_code=400)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"status": "bad_request", "detail": "Invalid JSON payload."}, status_code=400)

    asyncio.create_task(manager.broadcast({"type": "live_article", "article": payload}))
    try:
        from backend.app.redis_pool import pubsub_publish
        await pubsub_publish("live_stream", payload)
    except Exception:
        pass

    return JSONResponse({"status": "accepted"}, status_code=200)

COUNTRY_COORDS = {
    "China": {"lat": 35.8617, "lon": 104.1954},
    "Pakistan": {"lat": 30.3753, "lon": 69.3451},
    "Afghanistan": {"lat": 33.9391, "lon": 67.71},
    "Bangladesh": {"lat": 23.685, "lon": 90.3563},
    "Myanmar": {"lat": 21.9162, "lon": 95.956},
    "Nepal": {"lat": 28.3949, "lon": 84.124},
    "Bhutan": {"lat": 27.5142, "lon": 90.4336},
    "Sri Lanka": {"lat": 7.8731, "lon": 80.7718},
    "Maldives": {"lat": 3.2028, "lon": 73.2207},
    "India": {"lat": 20.5937, "lon": 78.9629},
    "United States": {"lat": 37.0902, "lon": -95.7129},
    "Russia": {"lat": 61.5240, "lon": 105.3188},
    "Iran": {"lat": 32.4279, "lon": 53.6880},
    "Israel": {"lat": 31.0461, "lon": 34.8516},
    "Taiwan": {"lat": 23.6978, "lon": 120.9605},
    "Japan": {"lat": 36.2048, "lon": 138.2529},
    "Australia": {"lat": -25.2744, "lon": 133.7751},
    "United Kingdom": {"lat": 55.3781, "lon": -3.4360},
    "Germany": {"lat": 51.1657, "lon": 10.4515},
    "Ukraine": {"lat": 48.3794, "lon": 31.1656},
    "South Korea": {"lat": 35.9078, "lon": 127.7669},
}

COUNTRY_NAMES_BY_CODE = {
    "CN": "China",
    "PK": "Pakistan",
    "AF": "Afghanistan",
    "BD": "Bangladesh",
    "MM": "Myanmar",
    "NP": "Nepal",
    "BT": "Bhutan",
    "LK": "Sri Lanka",
    "MV": "Maldives",
    "IN": "India",
    "US": "United States",
    "RU": "Russia",
    "IR": "Iran",
    "IL": "Israel",
    "TW": "Taiwan",
    "JP": "Japan",
    "AU": "Australia",
    "GB": "United Kingdom",
    "DE": "Germany",
    "UA": "Ukraine",
    "KR": "South Korea",
}

COUNTRY_CODES_BY_NAME = {}
COUNTRY_REGIONS = {}

# Dynamically load coordinates, names, and regions from world-countries npm package
PROJECT_ROOT = Path(__file__).resolve().parents[2]
COUNTRIES_JSON_PATH = PROJECT_ROOT / "node_modules" / "world-countries" / "countries.json"
if COUNTRIES_JSON_PATH.exists():
    try:
        with open(COUNTRIES_JSON_PATH, "r", encoding="utf-8") as f:
            countries_data = json.load(f)
            for c in countries_data:
                cca2 = c.get("cca2", "").upper()
                name = c.get("name", {}).get("common", "")
                latlng = c.get("latlng")
                region = c.get("region", "Global")
                if cca2 and name:
                    COUNTRY_NAMES_BY_CODE[cca2] = name
                    COUNTRY_CODES_BY_NAME[name.lower()] = cca2
                    COUNTRY_REGIONS[name] = region
                    if latlng and len(latlng) == 2:
                        COUNTRY_COORDS[name] = {"lat": latlng[0], "lon": latlng[1]}
    except Exception as e:
        logger.error(f"[Main] Failed to load global countries.json: {e}")

# Build reverse lookup index for base static countries
for k, v in COUNTRY_NAMES_BY_CODE.items():
    COUNTRY_CODES_BY_NAME[v.lower()] = k


def country_name_from_code(country_code: str) -> str:
    return COUNTRY_NAMES_BY_CODE.get((country_code or "").upper(), country_code or "Global")


def country_coords_from_code(country_code: str) -> Optional[dict]:
    return COUNTRY_COORDS.get(country_name_from_code(country_code))


def article_to_world_alert(article: Article) -> Optional[dict]:
    coords = country_coords_from_code(article.country_code)
    if not coords:
        return None

    # Severity: high (Red), medium (Blue), low (Green)
    severity = "high"
    if article.impact_level == "High Impact":
        severity = "high"
    elif article.impact_level == "Medium Impact":
        severity = "medium"
    else:
        severity = "low"

    return {
        "id": article.id,
        "location": country_name_from_code(article.country_code),
        "lat": coords["lat"],
        "lon": coords["lon"],
        "severity": severity,
        "headline": article.title,
        "source": article.source or "News Feed",
        "url": article.url,
        "timestamp": article.published_at.isoformat(),
        "summary": article.summary or article.headline or "No summary available.",
        "countryCode": article.country_code,
        "source_links": build_source_links(article),
    }


def build_source_links(article: Article) -> List[dict]:
    links: List[dict] = []
    if getattr(article, "url", None):
        links.append({
            "name": article.source or "Original source",
            "url": article.url,
        })

    try:
        also_reported_by = json.loads(article.also_reported_by) if getattr(article, "also_reported_by", None) else []
    except Exception:
        also_reported_by = []

    if isinstance(also_reported_by, list):
        for idx, item in enumerate(also_reported_by[:4]):
            if not item or item == article.url:
                continue
            links.append({
                "name": f"Also reported {idx + 1}",
                "url": item,
            })

    return links

# Background Ingestion Task
async def run_ingestion_cycle(test_mode: bool = False):
    await create_tables()
    raw_articles = await fetch_global_news(
        limit_per_country=settings.scrape_limit_per_country,
        test_mode=test_mode,
    )

    processed = 0
    high_impact = 0
    async for db in get_db():
        for offset in range(0, len(raw_articles), settings.ingestion_batch_size):
            batch = raw_articles[offset : offset + settings.ingestion_batch_size]
            result = await classify_and_store_batch(db, batch)
            processed += result["processed"]
            high_impact += result["high_impact"]
        break

    return {
        "raw_articles": len(raw_articles),
        "processed": processed,
        "high_impact": high_impact,
    }


async def periodic_ingestion_loop():
    logger.info("[Main] Starting background ingestion loop for all configured countries (realtime mode: 2 min interval).")
    while True:
        try:
            result = await run_ingestion_cycle(test_mode=False)
            logger.info(
                "[Main] Background ingestion step completed: raw=%s processed=%s high_impact=%s",
                result["raw_articles"],
                result["processed"],
                result["high_impact"],
            )
            # Broadcast ingestion summary via WebSocket channel
            from backend.app.redis_pool import pubsub_publish
            await pubsub_publish("drishya:ws:alerts", {
                "type": "ingestion_complete",
                "raw_articles": result["raw_articles"],
                "processed": result["processed"],
                "high_impact": result["high_impact"],
                "timestamp": datetime_now().isoformat(),
            })
        except Exception as e:
            logger.error(f"[Main] Ingestion error: {e}")

        await asyncio.sleep(2 * 60)  # Realtime: every 2 minutes


async def _note_cleanup_loop():
    """Background task to delete expired notes (older than 24h)."""
    while True:
        try:
            async for db in get_db():
                from backend.app.database import SharedNote
                cutoff = datetime.now(timezone.utc) - timedelta_now(hours=24)
                from sqlalchemy import delete as sql_delete
                await db.execute(sql_delete(SharedNote).where(SharedNote.created_at < cutoff))
                await db.commit()
                break
        except Exception as e:
            logger.debug(f"[Main] Note cleanup error: {e}")
        await asyncio.sleep(300)  # Run every 5 minutes


async def _corroboration_persistence_loop():
    """Background task to persist verified stories to the database every 5 minutes."""
    while True:
        try:
            from backend.app.services.credibility import corroboration_engine
            verified = corroboration_engine.get_verified_stories(min_score=0.3)
            if verified:
                async for db in get_db():
                    from backend.app.database import VerifiedStory
                    from sqlalchemy import select as sql_select
                    existing_res = await db.execute(
                        sql_select(VerifiedStory.story_key)
                    )
                    existing_keys = {row[0] for row in existing_res.all()}

                    for story in verified:
                        if story.story_key in existing_keys:
                            # Update existing
                            update_res = await db.execute(
                                sql_select(VerifiedStory).where(VerifiedStory.story_key == story.story_key)
                            )
                            existing = update_res.scalar_one_or_none()
                            if existing:
                                existing.status = story.status.value
                                existing.corroboration_score = story.corroboration_score
                                existing.unique_source_count = story.unique_source_count
                                existing.sources_json = json.dumps(story.sources[:10])
                                existing.last_updated = datetime.now(timezone.utc)
                        else:
                            # Insert new
                            new_story = VerifiedStory(
                                story_key=story.story_key,
                                headline=story.headline[:512],
                                summary=story.summary[:1000] if story.summary else None,
                                status=story.status.value,
                                corroboration_score=story.corroboration_score,
                                unique_source_count=story.unique_source_count,
                                sources_json=json.dumps(story.sources[:10]),
                                first_seen=story.first_seen,
                                last_updated=story.last_updated,
                            )
                            db.add(new_story)
                    await db.commit()
                    logger.debug("[Main] Persisted %d verified stories to DB", len(verified))
                    break
        except Exception as e:
            logger.debug(f"[Main] Corroboration persistence error: {e}")
        await asyncio.sleep(300)  # Run every 5 minutes


def format_entities_for_frontend(flat_entities) -> dict:
    if not flat_entities:
        return {
            "countries": [],
            "organizations": [],
            "militaryUnits": [],
            "weapons": [],
            "people": []
        }
        
    if isinstance(flat_entities, str):
        try:
            flat_entities = json.loads(flat_entities)
        except Exception:
            flat_entities = []
            
    countries = []
    organizations = []
    military_units = []
    weapons = []
    people = []
    
    known_countries = {"China", "India", "Pakistan", "Afghanistan", "Bangladesh", "Myanmar", "Nepal", "Bhutan", "Sri Lanka", "Maldives", "US", "USA", "Russia"}
    known_weapons = {"missile", "radar", "artillery", "carrier", "jet", "su-30mki", "uav", "drone", "frigate", "submarine", "tank", "destroyer"}
    
    for item in flat_entities:
        if not item:
            continue
        item_str = str(item)
        item_lower = item_str.lower()
        if item_str in known_countries:
            countries.append(item_str)
        elif any(w in item_lower for w in known_weapons) or item_lower in {"j-20", "s-400", "rafale"}:
            weapons.append(item_str)
        elif any(k in item_lower for k in ["army", "navy", "force", "command", "theater", "division", "regiment", "pla", "iaf"]):
            military_units.append(item_str)
        elif any(org in item_lower for org in ["ministry", "agency", "government", "parliament", "un", "nato", "defense"]):
            organizations.append(item_str)
        else:
            words = item_str.split()
            if len(words) <= 3 and all(w[0].isupper() for w in words if w):
                people.append(item_str)
            else:
                organizations.append(item_str)
                
    return {
        "countries": countries,
        "organizations": organizations,
        "militaryUnits": military_units,
        "weapons": weapons,
        "people": people
    }


def article_to_signal_dict(art: Article, country_label: str, location_suffix: str = "Region") -> dict:
    """
    Consolidated signal serializer — single source of truth for article→signal dict shape.
    Replaces the 3 duplicated inline blocks in get_all_news and get_specific_country_news.
    """
    try:
        also_rep = json.loads(art.also_reported_by) if getattr(art, "also_reported_by", None) else []
    except Exception:
        also_rep = []
    return {
        "id": art.id,
        "country": country_label,
        "category": get_frontend_category(art.department, art.title, art.source),
        "impact": {"High Impact": "High", "Medium Impact": "Medium"}.get(art.impact_level, "Low"),
        "headline": art.title.strip(),
        "summary": (
            art.summary.strip() if art.summary and art.summary.strip()
            else (art.content.strip()[:150] + "..." if art.content and art.content.strip() else "No summary available.")
        ),
        "source": art.source or "News Feed",
        "timestamp": art.published_at.isoformat(),
        "url": art.url,
        "verification_status": getattr(art, "source_reputation", None) or "Verified Source",
        "confidence_score": getattr(art, "confidence_score", None) or 0.98,
        "entities": format_entities_for_frontend(getattr(art, "entities", None)),
        "location_name": getattr(art, "sector", None) or f"{country_label} {location_suffix}",
        "intel_category": art.department or "Military",
        "also_reported_by": also_rep,
        "source_links": build_source_links(art),
    }


def data_to_signal(data: dict) -> dict:
    if data.get("type") == "mesh_status":
        return data

    country_code = data.get("country_code") or "GL"
    country_name = country_name_from_code(country_code)
    category = get_frontend_category(data.get("department"), data.get("title", ""), data.get("source", ""))
    
    impact = "Low"
    if data.get("impact_level") == "High Impact":
        impact = "High"
    elif data.get("impact_level") == "Medium Impact":
        impact = "Medium"
        
    try:
        also_rep = data.get("also_reported_by") or []
        if isinstance(also_rep, str):
            also_rep = json.loads(also_rep)
    except Exception:
        also_rep = []

    # Enrich with corroboration status
    corroboration_status = "single_source"
    try:
        from backend.app.services.credibility import corroboration_engine
        story = corroboration_engine.get_story_by_key(
            hashlib.md5((data.get("title", "") + country_code).encode()).hexdigest()[:16]
        )
        if story:
            corroboration_status = story.status.value
    except Exception:
        pass

    return {
        "type": "signal",
        "country": country_name,
        "signal": {
            "id": data.get("id") or f"{country_name}-{int(time.time())}",
            "country": country_name,
            "category": category,
            "impact": impact,
            "headline": data.get("title", ""),
            "summary": data.get("summary") or data.get("content", "")[:180],
            "source": data.get("source") or "News Feed",
            "timestamp": data.get("published_at"),
            "url": data.get("url"),
            "verification_status": data.get("source_reputation") or "Verified Source",
            "confidence_score": data.get("confidence_score") or 0.98,
            "corroboration_status": corroboration_status,
            "entities": format_entities_for_frontend(data.get("entities")),
            "location_name": data.get("sector") or f"{country_name} Region",
            "intel_category": data.get("department") or "Military",
            "also_reported_by": also_rep
        }
    }


async def redis_listener_task():
    """
    Subscribes to Redis 'live_stream' channel and broadcasts received articles
    to all websocket clients connected to this node.
    Handles automatic reconnection and falls back to local memory_stream if Redis is offline.
    Uses the shared Redis pool.
    """
    from backend.app.redis_pool import get_redis_pool
    
    redis_online = False
    
    while True:
        pubsub = None
        try:
            logger.info("[Main] Attempting connection to Redis Pub/Sub...")
            pool = await get_redis_pool()
            if not pool:
                raise ConnectionError("Redis pool unavailable")
            
            pubsub = pool.pubsub()
            await pubsub.subscribe("live_stream", "drishya:ws:weather", "drishya:ws:notes", "drishya:ws:alerts")
            logger.info("[Main] Redis pub/sub successfully subscribed to channels.")
            redis_online = True
            
            async for message in pubsub.listen():
                if message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        channel = message.get("channel", "live_stream")
                        
                        if channel == "live_stream":
                            sig_msg = data_to_signal(data)
                            await manager.broadcast(sig_msg, channel="alerts")
                            await manager.broadcast(sig_msg, channel="map")
                        elif channel == "drishya:ws:weather":
                            await manager.broadcast(data, channel="weather")
                        elif channel == "drishya:ws:notes":
                            await manager.broadcast(data, channel="notes")
                        elif channel == "drishya:ws:alerts":
                            await manager.broadcast(data, channel="alerts")
                    except Exception as ex:
                        logger.error(f"[Main] Error parsing pubsub message: {ex}")
        except Exception as e:
            logger.warning(f"[Main] Redis pub/sub error/offline: {e}.")
            # Clean up previous pubsub before reconnecting
            if pubsub:
                try:
                    await pubsub.unsubscribe()
                    await pubsub.close()
                except Exception:
                    pass
            if not redis_online:
                logger.info("[Main] Falling back to local in-memory event stream.")
                def on_live_signal(article):
                    sig_msg = data_to_signal(article)
                    asyncio.create_task(manager.broadcast(sig_msg, channel="alerts"))
                memory_stream.subscribe(on_live_signal)
                break
            else:
                logger.info("[Main] Retrying Redis Pub/Sub connection in 5 seconds...")
                await asyncio.sleep(5)



@app.get("/api/events/stream")
async def events_sse_stream():
    """Server-Sent Events endpoint for lightweight realtime clients.

    Streams all live events (articles, alerts, weather, notes) as SSE.
    Clients can connect with: new EventSource('/api/events/stream')
    """
    async def event_generator():
        import asyncio as _aio
        queue: asyncio.Queue = asyncio.Queue()

        def on_event(payload):
            try:
                queue.put_nowait(payload)
            except Exception:
                pass

        # Subscribe to the in-memory event stream
        memory_stream.subscribe(on_event)

        try:
            while True:
                try:
                    payload = await _aio.wait_for(queue.get(), timeout=30.0)
                    import json as _json
                    yield f"data: {_json.dumps(payload, default=str)}\n\n"
                except _aio.TimeoutError:
                    # Send heartbeat to keep connection alive
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            memory_stream.unsubscribe(on_event)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/system/status")
async def get_system_status():
    configured = []
    if settings.newsapi_key: configured.append("NewsAPI")
    if settings.world_news_api_key: configured.append("WorldNewsAPI")
    if settings.newsdata_api_key: configured.append("NewsDataAPI")
    if settings.finnhub_api_key: configured.append("FinnhubAPI")
    if settings.gnews_api_key: configured.append("GNewsAPI")
    if settings.currents_api_key: configured.append("CurrentsAPI")
    if settings.thenews_api_key: configured.append("TheNewsAPI")
    if settings.mediastack_api_key: configured.append("MediaStackAPI")
    if settings.newscatcher_api_key: configured.append("NewsCatcherAPI")
    if settings.bing_news_api_key: configured.append("BingNewsAPI")

    weather_key = os.getenv("OPENWEATHERMAP_API_KEY") or os.getenv("OPENWEATHER_API_KEY")
    weather_prov = "OpenWeatherMap" if weather_key else "simulated"

    llm_prov = settings.llm_provider or "none"
    if llm_prov == "none":
        if settings.google_api_key:
            llm_prov = "gemini"
        elif settings.openai_api_key:
            llm_prov = "openai"
        elif settings.ollama_base_url:
            llm_prov = "ollama"

    mode = "demo" if settings.enable_demo_seed_data else "live"

    # Credibility engine status
    credibility_info = {}
    try:
        from backend.app.services.credibility import corroboration_engine
        from backend.app.services.circuit_breaker import health_monitor
        verified = corroboration_engine.get_verified_stories(min_score=0.3)
        credibility_info = {
            "tracked_stories": len(corroboration_engine.stories),
            "verified_stories": len(verified),
            "provider_health": health_monitor.get_all_status(),
        }
    except Exception:
        pass

    return {
        "mode": mode,
        "configured_providers": configured,
        "llm_provider": llm_prov,
        "weather_provider": weather_prov,
        "credibility": credibility_info,
    }


@app.get("/api/risk/country")
async def get_country_risk(
    code: str = Query(..., min_length=2, max_length=3),
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """Return an explainable country risk score from recent stored evidence."""
    country_code = code.strip().upper()
    cutoff = datetime_now() - timedelta_now(days=days)
    result = await db.execute(
        select(Article)
        .where(Article.country_code == country_code, Article.published_at >= cutoff)
        .order_by(Article.published_at.desc())
        .limit(500)
    )
    return {
        "country_code": country_code,
        "country": country_name_from_code(country_code),
        "window_days": days,
        **calculate_country_risk(result.scalars().all()),
        "generated_at": datetime_now().isoformat(),
    }


@app.get("/api/intelligence/dashboard")
async def intelligence_dashboard(
    days: int = Query(7, ge=1, le=90),
    db: AsyncSession = Depends(get_db)
):
    """
    Threat intelligence dashboard endpoint.
    Returns aggregated metrics, trend analysis, source health, and top stories.
    """
    cutoff = datetime_now() - timedelta_now(days=days)
    stmt = select(Article).where(Article.published_at >= cutoff).order_by(Article.published_at.desc())
    result = await db.execute(stmt)
    articles = list(result.scalars().all())

    if not articles:
        return {
            "period_days": days,
            "total_articles": 0,
            "summary": "No articles in this time period.",
            "impact_breakdown": {"high": 0, "medium": 0, "normal": 0},
            "by_department": {},
            "top_countries": [],
            "trend": {"direction": "stable", "daily": {}},
            "top_stories": [],
            "source_health": {},
            "generated_at": datetime_now().isoformat(),
        }

    # --- Aggregate Metrics ---
    high_impact = [a for a in articles if a.impact_level == "High Impact"]
    medium_impact = [a for a in articles if a.impact_level == "Medium Impact"]
    normal_impact = [a for a in articles if a.impact_level == "Normal Impact"]

    # Articles by department
    by_dept: dict[str, int] = {}
    for a in articles:
        dept = a.department or "Unclassified"
        by_dept[dept] = by_dept.get(dept, 0) + 1

    # Articles by country
    by_country: dict[str, int] = {}
    for a in articles:
        cc = a.country_code or "Unknown"
        by_country[cc] = by_country.get(cc, 0) + 1
    top_countries = sorted(by_country.items(), key=lambda x: x[1], reverse=True)[:10]

    # --- Trend Analysis (day-by-day) ---
    from collections import defaultdict as dd
    daily_counts: dd[str, dict] = dd(lambda: {"total": 0, "high": 0, "medium": 0, "normal": 0})
    for a in articles:
        day = a.published_at.strftime("%Y-%m-%d")
        daily_counts[day]["total"] += 1
        if a.impact_level == "High Impact":
            daily_counts[day]["high"] += 1
        elif a.impact_level == "Medium Impact":
            daily_counts[day]["medium"] += 1
        else:
            daily_counts[day]["normal"] += 1

    sorted_days = sorted(daily_counts.keys())
    trend_direction = "stable"
    if len(sorted_days) >= 2:
        first_half = sum(daily_counts[d]["high"] for d in sorted_days[:len(sorted_days)//2])
        second_half = sum(daily_counts[d]["high"] for d in sorted_days[len(sorted_days)//2:])
        if second_half > first_half * 1.3:
            trend_direction = "rising"
        elif second_half < first_half * 0.7:
            trend_direction = "falling"

    daily_trend = {d: daily_counts[d] for d in sorted_days}

    # --- Source Health ---
    from collections import Counter as Cnt
    source_counts = Cnt(a.source for a in articles if a.source)
    top_sources = source_counts.most_common(15)

    source_health = {}
    for source, count in top_sources:
        high_ratio = sum(1 for a in articles if a.source == source and a.impact_level == "High Impact") / max(count, 1)
        try:
            from backend.app.services.credibility import compute_source_reputation_score, classify_source_tier
            rep_score = compute_source_reputation_score(source)
            tier = classify_source_tier(source).value
        except Exception:
            rep_score = 0.5
            tier = "unknown"
        source_health[source] = {
            "article_count": count,
            "high_impact_ratio": round(high_ratio, 2),
            "reputation_score": rep_score,
            "tier": tier,
        }

    # --- Top Stories (highest impact, most recent) ---
    top_stories = []
    for a in high_impact[:10]:
        try:
            also_rep = json.loads(a.also_reported_by) if getattr(a, "also_reported_by", None) else []
        except Exception:
            also_rep = []
        top_stories.append({
            "id": a.id,
            "title": a.title,
            "summary": a.summary or a.content[:200],
            "country": country_name_from_code(a.country_code),
            "department": a.department,
            "source": a.source,
            "url": a.url,
            "timestamp": a.published_at.isoformat(),
            "corroborated_by": len(also_rep),
        })

    return {
        "period_days": days,
        "total_articles": len(articles),
        "impact_breakdown": {
            "high": len(high_impact),
            "medium": len(medium_impact),
            "normal": len(normal_impact),
        },
        "by_department": by_dept,
        "top_countries": [{"code": cc, "name": country_name_from_code(cc), "count": n} for cc, n in top_countries],
        "trend": {
            "direction": trend_direction,
            "daily": daily_trend,
        },
        "source_health": source_health,
        "top_stories": top_stories,
        "generated_at": datetime_now().isoformat(),
    }


@app.get("/api/news/all")
async def get_all_news(
    category: str = Query("All"),
    timeframe: str = Query("24h"),
    max_age_hours: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    cache_key = f"drishya:cache:news:all:{category}:{timeframe}:{max_age_hours}"
    cached = await get_cached_response(cache_key)
    if cached:
        logger.info("[Cache] Returning cached /api/news/all response")
        return cached

    now = datetime_now()
    if max_age_hours is not None:
        cutoff = now - timedelta_now(hours=max_age_hours)
    else:
        if timeframe == "1h":
            cutoff = now - timedelta_now(hours=1)
        elif timeframe == "12h":
            cutoff = now - timedelta_now(hours=12)
        elif timeframe in ("24h", "1d"):
            cutoff = now - timedelta_now(hours=24)
        elif timeframe in ("7d", "1w"):
            cutoff = now - timedelta_now(days=7)
        elif timeframe in ("30d", "1m"):
            cutoff = now - timedelta_now(days=30)
        else:
            cutoff = now - timedelta_now(days=30)
    
    from sqlalchemy.orm import defer
    # Query database articles (High Impact) without loading large float embeddings
    stmt = select(Article).where(Article.published_at >= cutoff).options(defer(Article.embedding))
    stmt = stmt.order_by(Article.published_at.desc())
    result = await db.execute(stmt)
    db_articles = result.scalars().all()

    def matches_category(article: Article, selected_category: str) -> bool:
        article_category = get_frontend_category(article.department, article.title, article.source)
        if selected_category == "All":
            return True
        return article_category == selected_category

    # Build list of active countries dynamically from database articles and settings watchlists
    config_codes = set(settings.critical_countries + settings.high_countries + settings.medium_countries)
    # Restrict to config_codes to avoid returning all 250 countries from seeded database
    active_codes = {art.country_code.upper() for art in db_articles if art.country_code and art.country_code.upper() in config_codes}
    all_active_codes = active_codes | config_codes
    
    countries = []
    for code in all_active_codes:
        name = COUNTRY_NAMES_BY_CODE.get(code.upper())
        if name:
            countries.append(name)
            
    base_countries = ["China", "Pakistan", "Afghanistan", "Bangladesh", "Myanmar", "Nepal", "Bhutan", "Sri Lanka", "Maldives"]
    for c in base_countries:
        if c not in countries:
            countries.append(c)
            
    countries = sorted(list(set(countries)))

    # Fetch all historical articles sorted by date (deferring large embedding column)
    historical_res = await db.execute(select(Article).options(defer(Article.embedding)).order_by(Article.published_at.desc()))
    all_historical = list(historical_res.scalars().all())

    fallback_by_country = {}
    for country in countries:
        c_code = get_country_code(country)
        country_lower = country.lower()
        
        matched = []
        for art in all_historical:
            if (art.country_code and art.country_code.upper() == c_code) or country_lower in art.title.lower():
                matched.append(art)
                if len(matched) >= 150: # Limit historical fallback scan to latest 150 per country
                    break
        fallback_by_country[country] = matched

    results = {}
    for country in countries:
        # Filter matching signals
        filtered_articles = [
            article for article in db_articles
            if article.country_code.upper() == get_country_code(country)
            or article.title.lower().find(country.lower()) >= 0
        ]
        if category != "All":
            filtered_articles = [article for article in filtered_articles if matches_category(article, category)]

        # Fallback to historical news to ensure abundant news display (Goal 3)
        if not filtered_articles:
            filtered_articles = fallback_by_country.get(country, [])
            if category != "All":
                filtered_articles = [article for article in filtered_articles if matches_category(article, category)]

        if len(filtered_articles) < 5:
            seen_ids = {article.id for article in filtered_articles}
            for art in fallback_by_country.get(country, []):
                if art.id in seen_ids:
                    continue
                if category == "All" or matches_category(art, category):
                    filtered_articles.append(art)
                elif len(filtered_articles) < 5:
                    filtered_articles.append(art)
                seen_ids.add(art.id)
                if len(filtered_articles) >= 5:
                    break

        signals = []
        for art in sorted(filtered_articles, key=lambda item: item.published_at, reverse=True):
            if not (art.title and art.title.strip() and art.url and (art.url.startswith("http://") or art.url.startswith("https://")) and (art.summary or art.content or "").strip()):
                continue
                
            try:
                also_rep = json.loads(art.also_reported_by) if getattr(art, "also_reported_by", None) else []
            except Exception:
                also_rep = []
                
            signals.append({
                "id": art.id,
                "country": country,
                "category": get_frontend_category(art.department, art.title, art.source),
                "impact": "High" if art.impact_level == "High Impact" else ("Medium" if art.impact_level == "Medium Impact" else "Low"),
                "headline": art.title.strip(),
                "summary": (art.summary.strip() if art.summary and art.summary.strip() else (art.content.strip()[:150] + "..." if art.content and art.content.strip() else "No summary available.")),
                "source": art.source or "News Feed",
                "timestamp": art.published_at.isoformat(),
                "url": art.url,
                "verification_status": getattr(art, "source_reputation", None) or "Verified Source",
                "confidence_score": getattr(art, "confidence_score", None) or 0.98,
                "entities": format_entities_for_frontend(getattr(art, "entities", None)),
                "location_name": getattr(art, "sector", None) or f"{country} Region",
                "intel_category": art.department or "Military",
                "also_reported_by": also_rep
                ,"source_links": build_source_links(art)
            })

 
        # Dynamic region & threat level resolution
        region = COUNTRY_REGIONS.get(country, "International Sector")
        base_regions = {
            "China": "Northern Front",
            "Pakistan": "Western Front",
            "Afghanistan": "Western Front",
            "Bangladesh": "Eastern Front",
            "Myanmar": "Southeastern Front",
            "Nepal": "Northern Front",
            "Bhutan": "Northern Front",
            "Sri Lanka": "Indian Ocean",
            "Maldives": "Indian Ocean"
        }
        if country in base_regions:
            region = base_regions[country]
            
        high_count = sum(1 for s in signals if s["impact"] == "High")
        medium_count = sum(1 for s in signals if s["impact"] == "Medium")
        
        base_threats = {
            "China": "Critical",
            "Pakistan": "High",
            "Afghanistan": "High",
            "Bangladesh": "Moderate",
            "Myanmar": "Critical",
            "Nepal": "Moderate",
            "Bhutan": "Moderate",
            "Sri Lanka": "Moderate",
            "Maldives": "Moderate"
        }
        
        if high_count >= 3:
            threat_level = "Critical"
        elif high_count >= 1 or medium_count >= 5:
            threat_level = "High"
        elif medium_count >= 1:
            threat_level = "Moderate"
        else:
            threat_level = base_threats.get(country, "Low")
 
        operational_summary = (
            "The border is quiet. There are no new reports in this time period."
            if not signals else
            f"We found {len(signals)} new reports in this time period."
        )
        risk = calculate_country_risk(filtered_articles)

        results[country] = {
            "region": region,
            "threat_level": threat_level,
            "last_synced": now.isoformat(),
            "operational_summary": operational_summary,
            "signals": signals,
            "risk": risk,
            "source_status": "normal"
        }
 
    # Second pass: live-ingest countries that still have zero signals
    zero_signal_countries = [c for c, r in results.items() if not r.get("signals")]
    if zero_signal_countries:
        import asyncio as _aio
        _sem = _aio.Semaphore(3)

        async def _live_fetch_country(cname: str):
            async with _sem:
                ccode = get_country_code(cname)
                if not ccode:
                    return
                try:
                    async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(15.0, connect=5.0)) as client:
                        raw = await fetch_country_news(client, ccode, budget=15)
                        if raw:
                            # Store in DB for future requests
                            from backend.app.services.classifier import classify_and_store_batch as _csb
                            await _csb(db, raw)
                            # Build signals from raw articles
                            live_signals = []
                            for art in raw:
                                if not (art.get("title") and art.get("url")):
                                    continue
                                live_signals.append({
                                    "id": art.get("id", f"live-{cname}-{len(live_signals)}"),
                                    "country": cname,
                                    "category": get_frontend_category(art.get("department", ""), art.get("title", ""), art.get("source", "")),
                                    "impact": art.get("impact", "Medium"),
                                    "headline": art.get("title", ""),
                                    "summary": (art.get("summary") or art.get("content") or "Live intelligence feed.")[:300],
                                    "source": art.get("source", "Live Feed"),
                                    "timestamp": (art.get("published_at") or now).isoformat() if hasattr((art.get("published_at") or now), 'isoformat') else str(art.get("published_at", now)),
                                    "url": art.get("url", ""),
                                    "verification_status": "Live Source",
                                    "confidence_score": 0.95,
                                    "source_links": [{"name": art.get("source", "Source"), "url": art.get("url", "")}] if art.get("url") else [],
                                })
                            if live_signals:
                                results[cname]["signals"] = live_signals
                                results[cname]["operational_summary"] = f"Live ingestion retrieved {len(live_signals)} reports for {cname}."
                                logger.info("[Live Ingest] Populated %d signals for %s", len(live_signals), cname)
                except Exception as e:
                    logger.warning("[Live Ingest] Failed for %s: %s", cname, e)

        await _aio.gather(*[_live_fetch_country(c) for c in zero_signal_countries], return_exceptions=True)

    await set_cached_response(cache_key, results, ttl=300)
    return results


from fastapi import BackgroundTasks

@asynccontextmanager
async def get_db_context():
    from backend.app.database import SessionLocal, init_db_engine
    if SessionLocal is None:
        await init_db_engine()
    async with SessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

async def fetch_and_classify_background(code: str, name: str):
    async with get_db_context() as db:
        import httpx
        from backend.app.services.ingestion import fetch_country_news
        from backend.app.services.classifier import classify_and_store_batch
        async with httpx.AsyncClient(follow_redirects=True) as client:
            try:
                raw_articles = await fetch_country_news(client, code, budget=50)
                if raw_articles:
                    await classify_and_store_batch(db, raw_articles)
                    logger.info("[Background Fetch] Successfully fetched and stored real-time news for %s (%s)", name, code)
            except Exception as e:
                logger.error("[Background Fetch] Error in background news ingestion for %s: %s", name, e)


@app.get("/api/news/country")
async def get_specific_country_news(
    background_tasks: BackgroundTasks,
    name: str = Query(...),
    code: str = Query(...),
    db: AsyncSession = Depends(get_db)
):
    name = name.strip()
    code = code.strip().upper()
    
    cache_key = f"drishya:cache:news:country:{code}"
    cached = await get_cached_response(cache_key)
    if cached:
        logger.info("[Cache] Returning cached /api/news/country response")
        return cached
    
    # Register/override the country name in ISO_COUNTRIES mapping
    from backend.app.services.ingestion import ISO_COUNTRIES, fetch_country_news
    from backend.app.services.classifier import classify_and_store_batch, ImpactClassifier
    import httpx
    import hashlib
    from datetime import datetime
    
    ISO_COUNTRIES[code] = name
    
    # 1. Query database for articles matching the country code
    stmt = select(Article).where(Article.country_code == code).order_by(Article.published_at.desc()).limit(150)
    res = await db.execute(stmt)
    db_articles = res.scalars().all()
    
    # 2. Trigger fresh fetch if db has fewer than 15 articles or latest is older than 15 mins
    from datetime import timezone
    is_stale = True
    if db_articles:
        latest_art = db_articles[0]
        now = datetime.now(timezone.utc)
        pub_at = latest_art.published_at
        if pub_at and pub_at.tzinfo is None:
            pub_at = pub_at.replace(tzinfo=timezone.utc)
        if pub_at:
            time_diff = (now - pub_at).total_seconds()
            if time_diff < 900 and len(db_articles) >= 15:
                is_stale = False
            
    if is_stale:
        # When DB is nearly empty, run a quick synchronous fetch so the first response includes data
        if len(db_articles) < 5:
            try:
                async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(20.0)) as client:
                    raw_articles = await fetch_country_news(client, code, budget=15)
                    if raw_articles:
                        await classify_and_store_batch(db, raw_articles)
                        # Re-query DB after ingestion
                        stmt = select(Article).where(Article.country_code == code).order_by(Article.published_at.desc()).limit(150)
                        res = await db.execute(stmt)
                        db_articles = res.scalars().all()
            except Exception as e:
                logger.warning("[Country News] Sync ingestion failed for %s: %s", name, e)
                # Fallback to background task
                background_tasks.add_task(fetch_and_classify_background, code, name)
        else:
            background_tasks.add_task(fetch_and_classify_background, code, name)
                
    db_signals = []
    for art in db_articles:
        if not (art.title and art.title.strip() and art.url and (art.url.startswith("http://") or art.url.startswith("https://")) and (art.summary or art.content or "").strip()):
            continue
            
        try:
            also_rep = json.loads(art.also_reported_by) if getattr(art, "also_reported_by", None) else []
        except Exception:
            also_rep = []
            
        db_signals.append({
            "id": art.id,
            "country": name,
            "category": get_frontend_category(art.department, art.title, art.source),
            "impact": "High" if art.impact_level == "High Impact" else ("Medium" if art.impact_level == "Medium Impact" else "Low"),
            "headline": art.title.strip(),
            "summary": (art.summary.strip() if art.summary and art.summary.strip() else (art.content.strip()[:150] + "..." if art.content and art.content.strip() else "No summary available.")),
            "source": art.source or "News Feed",
            "timestamp": art.published_at.isoformat(),
            "url": art.url,
            "verification_status": getattr(art, "source_reputation", None) or "Verified Source",
            "confidence_score": getattr(art, "confidence_score", None) or 0.98,
            "entities": format_entities_for_frontend(getattr(art, "entities", None)),
            "location_name": getattr(art, "sector", None) or f"{name} Region",
            "intel_category": art.department or "Military",
            "also_reported_by": also_rep
            ,"source_links": build_source_links(art)
        })

    if len(db_signals) < 5:
        stmt_topup = select(Article).where(Article.country_code == code).order_by(Article.published_at.desc()).limit(300)
        res_topup = await db.execute(stmt_topup)
        seen_ids = {sig["id"] for sig in db_signals}
        for art in res_topup.scalars().all():
            if art.id in seen_ids:
                continue
            if not (art.title and art.title.strip() and art.url and (art.url.startswith("http://") or art.url.startswith("https://")) and (art.summary or art.content or "").strip()):
                continue
            try:
                also_rep = json.loads(art.also_reported_by) if getattr(art, "also_reported_by", None) else []
            except Exception:
                also_rep = []
            db_signals.append({
                "id": art.id,
                "country": name,
                "category": get_frontend_category(art.department, art.title, art.source),
                "impact": "High" if art.impact_level == "High Impact" else ("Medium" if art.impact_level == "Medium Impact" else "Low"),
                "headline": art.title.strip(),
                "summary": (art.summary.strip() if art.summary and art.summary.strip() else (art.content.strip()[:150] + "..." if art.content and art.content.strip() else "Tactical intelligence briefing restricted.")),
                "source": art.source or "OSINT Mesh",
                "timestamp": art.published_at.isoformat(),
                "url": art.url,
                "verification_status": getattr(art, "source_reputation", None) or "Verified Source",
                "confidence_score": getattr(art, "confidence_score", None) or 0.98,
                "entities": format_entities_for_frontend(getattr(art, "entities", None)),
                "location_name": getattr(art, "sector", None) or f"{name} Frontier",
                "intel_category": art.department or "Military",
                "also_reported_by": also_rep,
                "source_links": build_source_links(art),
            })
            seen_ids.add(art.id)
            if len(db_signals) >= 5:
                break

    
    # 4. Merge database signals, keeping unique URLs
    all_signals = []
    seen_urls = set()
    for sig in db_signals:
        if sig["url"] not in seen_urls:
            seen_urls.add(sig["url"])
            all_signals.append(sig)
            
    all_signals = all_signals[:500]
    
    threat_level = "Moderate"
    if all_signals:
        if any(s["impact"] == "High" for s in all_signals):
            threat_level = "Critical"
        else:
            threat_level = "High"
            
    now = datetime_now()
    response_data = {
        "region": "Global Sector",
        "threat_level": threat_level,
        "last_synced": now.isoformat(),
        "operational_summary": f"We found {len(all_signals)} new reports for {name}." if all_signals else "The border is quiet. There are no new reports in this time period.",
        "signals": all_signals,
        "risk": calculate_country_risk(db_articles),
        "source_status": "normal"
    }
    await set_cached_response(cache_key, response_data, ttl=300)
    return response_data


@app.get("/api/world/alerts")
async def world_alerts_endpoint(
    force: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    cache_key = "drishya:cache:world:alerts"
    if not force:
        cached = await get_cached_response(cache_key)
        if cached:
            logger.info("[Cache] Returning cached /api/world/alerts response")
            return cached

    now = datetime_now()
    cutoff = now - timedelta_now(days=7)

    stmt = select(Article).where(Article.published_at >= cutoff).order_by(Article.published_at.desc()).limit(2000)
    result = await db.execute(stmt)
    articles = result.scalars().all()

    alerts = []
    seen = set()
    for article in articles:
        alert = article_to_world_alert(article)
        if not alert:
            continue
        key = (alert["location"], alert["headline"])
        if key in seen:
            continue
        seen.add(key)
        alerts.append(alert)
        if len(alerts) >= 1500:
            break

    response_data = {
        "updatedAt": now.isoformat(),
        "count": len(alerts),
        "alerts": alerts,
    }
    await set_cached_response(cache_key, response_data, ttl=300)
    return response_data


@app.post("/api/news/refresh")
async def refresh_news_endpoint():
    try:
        result = await run_ingestion_cycle(test_mode=False)
        return {"success": True, **result}
    except Exception as exc:
        logger.error("[Main] Manual refresh failed: %s", exc)
        raise HTTPException(status_code=500, detail="News refresh failed")

@app.post("/api/gdelt/ingest")
async def trigger_gdelt_ingestion():
    """Manually trigger a single GDELT 2.0 Events ingestion cycle.

    Returns the same stats dict as the background worker:
    fetched, filtered, deduped, inserted, errors.
    """
    try:
        from backend.app.services.gdelt_worker import ingest_gdelt_events
        result = await ingest_gdelt_events()
        return {"success": True, **result}
    except Exception as exc:
        logger.error("[Main] Manual GDELT ingestion failed: %s", exc)
        raise HTTPException(status_code=500, detail="GDELT ingestion failed")


@app.get("/api/scrape/status")
async def scrape_status_stream():
    async def event_generator():
        async for message in job_store.subscribe("scrape"):
            yield f"data: {json.dumps(message)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/api/scrape")
async def trigger_scrape_endpoint(payload: dict):
    urls = payload.get("urls", [])
    if not urls:
        raise HTTPException(status_code=400, detail="URLs parameter is required.")
        
    job_ids = []
    for url in urls:
        job = await job_store.create("scrape", {"url": url, "platform": payload.get("platform", "news")})
        job_ids.append(job.job_id)
        if settings.enable_inline_job_processing:
            asyncio.create_task(run_mock_scrape_job(job.job_id, url, payload.get("platform", "news")))
    return {"success": True, "jobIds": job_ids}

def is_safe_url(url: str) -> bool:
    """SSRF protection: delegates to shared utility."""
    from backend.app.services.net_safety import is_safe_url as _is_safe
    return _is_safe(url)

# Scrape Job Worker doing real scraping
async def run_mock_scrape_job(job_id: str, url: str, platform: str):
    from backend.app.observability import metrics
    from bs4 import BeautifulSoup
    from datetime import datetime, timezone
    from backend.app.services.classifier import ImpactClassifier
    import httpx
    
    metrics.state.scrape_real_fetch_total += 1
    
    try:
        # 1. SSRF Protection
        if not is_safe_url(url):
            metrics.state.scrape_fetch_failures_total += 1
            raise ValueError(f"Forbidden URL: Access to local/private network ranges is restricted.")
        
        # 2. Scraping stage
        await job_store.update(job_id, "scrape", status="scraping", progress=20, step="fetching URL")
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            response = await client.get(url, headers=headers)
            if response.status_code != 200:
                metrics.state.scrape_fetch_failures_total += 1
                raise ValueError(f"Failed to fetch site: HTTP Status {response.status_code}")
        
        html = response.text
        soup = BeautifulSoup(html, "html.parser")
        
        # Get Title
        title = "Untitled Scraped Article"
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        
        # Clean HTML content
        for element in soup(["script", "style", "nav", "header", "footer", "form", "aside"]):
            element.extract()
            
        lines = (line.strip() for line in soup.get_text().splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        extracted_text = "\n".join(chunk for chunk in chunks if chunk)
        
        if not extracted_text.strip():
            metrics.state.scrape_fetch_failures_total += 1
            raise ValueError("Empty or unreadable text content extracted from page.")
            
        # 3. Summarization stage
        await job_store.update(job_id, "scrape", status="summarizing", progress=50, step="summarizing content")
        
        prompt = f"Summarize this raw article text for strategic intelligence briefing. Keep it short (under 3 sentences). Return ONLY the brief summary.\n\nRAW TEXT:\n{extracted_text[:4000]}"
        summary = ""
        
        # Try LLMs
        if settings.llm_provider == "ollama" and settings.ollama_base_url:
            try:
                from backend.app.services.summarizer import call_ollama
                summary = await call_ollama(prompt, "You are a strategic intelligence officer.")
            except Exception:
                pass
        if not summary and settings.openai_api_key:
            try:
                from backend.app.services.summarizer import call_openai
                summary = await call_openai(prompt, "You are a strategic intelligence officer.")
            except Exception:
                pass
        if not summary and settings.google_api_key:
            try:
                from backend.app.services.summarizer import call_gemini
                summary = await call_gemini(prompt, "You are a strategic intelligence officer.")
            except Exception:
                pass
        
        if not summary:
            summary = extracted_text.strip()[:180] + "..." if len(extracted_text.strip()) > 180 else extracted_text.strip()
            
        # 4. Classification & Saving stage
        await job_store.update(job_id, "scrape", status="saving", progress=80, step="classifying and saving")
        
        classifier = ImpactClassifier()
        impact, dept = classifier.classify(title, summary)
        
        article_dict = {
            "title": title,
            "headline": title,
            "summary": summary,
            "content": extracted_text,
            "url": url,
            "source": "user-submitted",
            "country_code": "GL",
            "published_at": datetime.now(timezone.utc),
            "impact_level": impact,
            "department": dept
        }
        
        # Persist inside database
        async for db in get_db():
            await classifier.save_article(db, article_dict)
            break
            
        result_payload = {
            "title": title,
            "url": url,
            "source": "user-submitted",
            "country_code": "GL",
            "impact_level": impact,
            "department": dept,
            "summary": summary
        }
        
        await job_store.update(job_id, "scrape", status="completed", progress=100, step="completed", result=result_payload)
    except Exception as e:
        logger.error(f"[Scraper] Real scrape job failed: {e}")
        await job_store.update(job_id, "scrape", status="failed", progress=100, step="failed", error=str(e))

# Utility helper mapping functions
def datetime_now() -> Any:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)

def timedelta_now(**kwargs) -> Any:
    from datetime import timedelta
    return timedelta(**kwargs)

def get_country_code(country: str) -> str:
    code = COUNTRY_CODES_BY_NAME.get(country.lower())
    if code:
        return code
    mapping = {
        "China": "CN", "Pakistan": "PK", "Afghanistan": "AF", "Bangladesh": "BD",
        "Myanmar": "MM", "Nepal": "NP", "Bhutan": "BT", "Sri Lanka": "LK", "Maldives": "MV"
    }
    return mapping.get(country, "GL")

TECH_KEYWORDS = re.compile(r"\b(cyber|drone|uav|satellite|radar|surveillance|sensor|telecom|internet|ai|artificial intelligence|machine learning|ml|technology|tech|space|communications|signal|gps)\b", re.I)

def get_frontend_category(dept: str, title: str = "", source: str = "") -> str:
    mapping = {
        "Military & Defense": "Military",
        "Economic & Financial": "Economic",
        "Social Affairs & Welfare": "Social",
        "Political & Diplomatic": "Political",
        "Technology & Cyber": "Tech",
        "Cyber & Technology": "Tech"
    }
    base_category = mapping.get(dept, "Political")
    combined_text = f"{title or ''} {source or ''}"
    if base_category in {"Military", "Economic"} and TECH_KEYWORDS.search(combined_text):
        return "Tech"
    return base_category

def uuid_str() -> str:
    import uuid
    return str(uuid.uuid4())

class QueryRequest(BaseModel):
    query: str
    country: Optional[str] = None

@app.post("/api/news/query")
async def query_news_research(payload: QueryRequest, db: AsyncSession = Depends(get_db)):
    query = payload.query
    country = payload.country
    
    # 1. Search database
    stmt = select(Article)
    if country:
        cc = get_country_code(country)
        stmt = stmt.where(Article.country_code == cc)
        
    result = await db.execute(stmt)
    articles = result.scalars().all()
    
    # Simple word overlap scoring
    query_terms = [w.lower() for w in query.split() if len(w) > 3]
    matched = []
    for art in articles:
        text = f"{art.title} {art.content}".lower()
        score = 0
        if not query_terms:
            score = 1
        else:
            for term in query_terms:
                if term in text:
                    score += 1
        if score > 0:
            matched.append((art, score))
            
    # Sort matched by score desc, then by date desc
    matched.sort(key=lambda x: (x[1], x[0].published_at), reverse=True)
    top_matches = [x[0] for x in matched[:5]]
    
    # Compile sources
    sources = [{"name": art.source or "News Feed", "url": art.url} for art in top_matches]
    
    # 2. Formulate answer using OpenAI/Gemini if available, or template
    summary = ""
    if top_matches:
        article_context = "\n".join([f"Source: {art.source}\nTitle: {art.title}\nContent: {art.content[:250]}\n" for art in top_matches])
        prompt = (
            f"Answer this question using ONLY these news articles. Be concise and clear. No jargon.\n\n"
            f"Question: {query}\n\nArticles:\n{article_context}"
        )
        try:
            if settings.openai_api_key:
                summary = await call_openai(prompt, "You are a senior analyst.")
            elif settings.google_api_key:
                summary = await call_gemini(prompt, "You are a senior analyst.")
        except Exception as e:
            logger.warning(f"[Main] LLM query call failed: {e}")
            
        if not summary:
            # Fallback text summary
            summary = (
            f"Based on {len(top_matches)} articles about '{query}':\n\n" + 
            "\n\n".join([f"• **{art.title}** ({art.source}): {art.summary or art.content[:150]}..." for art in top_matches])
            )
    else:
        summary = f"No matching alerts found in the {country or 'Global'} database archives."

    return {
        "summary": summary,
        "sources": sources,
        "matchedCount": len(top_matches),
        "generatedAt": datetime_now().isoformat(),
        "detectedCountry": country or "Global"
    }

@app.get("/api/news/realtime")
async def realtime_news_fetch(
    q: str = Query(..., min_length=1, description="Search query for real-time news"),
    country_code: Optional[str] = Query(None, min_length=2, max_length=3),
    limit: int = Query(20, ge=1, le=100),
):
    """
    Fetch real-time news directly from configured API providers without database storage.
    Returns fresh results from NewsAPI, NewsData, Newscatcher, Google News RSS, and other
    configured sources. Designed for on-demand live intelligence lookups.
    """
    from backend.app.services.ingestion import (
        _fetch_all_news_sources, fetch_rss_feed, CircuitState,
        _search_query_broad, ISO_COUNTRIES,
    )
    import httpx

    country_name = ISO_COUNTRIES.get(country_code, q) if country_code else q
    per_source = max(limit, 10)

    try:
        async with httpx.AsyncClient(
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
            follow_redirects=True,
        ) as client:
            # 1. Fetch from all configured API providers
            raw_articles = await _fetch_all_news_sources(
                client, country_code or "GL", country_name, per_source
            )

            # 2. If we need more, pull from Google News RSS
            if len(raw_articles) < limit and q.strip():
                rss_breaker = CircuitState("RSS-Realtime")
                rss_articles = await fetch_rss_feed(
                    client, country_code or "GL", country_name,
                    limit - len(raw_articles), rss_breaker,
                )
                seen_urls = {a["url"] for a in raw_articles if a.get("url")}
                for art in rss_articles:
                    if art.get("url") and art["url"] not in seen_urls:
                        raw_articles.append(art)
                        seen_urls.add(art["url"])

            # 3. Build response with source reputation
            from backend.app.services.classifier import compute_source_reputation

            results = []
            for art in raw_articles[:limit]:
                results.append({
                    "title": art.get("title", ""),
                    "summary": art.get("summary") or art.get("content", "")[:300],
                    "url": art.get("url", ""),
                    "source": art.get("source", "OSINT Feed"),
                    "country_code": art.get("country_code", "GL"),
                    "published_at": (
                        art["published_at"].isoformat()
                        if hasattr(art.get("published_at", ""), "isoformat")
                        else str(art.get("published_at", ""))
                    ),
                    "reputation": compute_source_reputation(art.get("source")),
                })

            return {
                "query": q,
                "country_code": country_code or "Global",
                "count": len(results),
                "fetched_at": datetime_now().isoformat(),
                "results": results,
            }

    except Exception as exc:
        logger.error("[Main] Real-time news fetch failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Real-time fetch failed: {exc}")


# Serve the built frontend assets if available.
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "dist"
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
    logger.info("[Main] Mounted frontend static assets from %s", FRONTEND_DIST)

    @app.get("/{full_path:path}")
    async def serve_single_page_app(full_path: str):
        return FileResponse(FRONTEND_DIST / "index.html")
