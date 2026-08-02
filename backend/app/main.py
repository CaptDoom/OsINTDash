import os
import asyncio
import json
import logging
import re
import time
import hashlib
from pathlib import Path
from typing import Dict, Any, List, Set, Optional
from pydantic import BaseModel
from fastapi import FastAPI, Depends, Query, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, PlainTextResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.config import settings
from backend.app.database import create_tables, get_db, Article
from backend.app.api.routes import archive, chat
from backend.app.services.ingestion import fetch_global_news
from backend.app.services.classifier import ImpactClassifier, classify_and_store_batch, memory_stream
from backend.app.services.summarizer import call_openai, call_gemini
from backend.app.observability import configure_logging, metrics, request_id_var
from backend.app.services.job_store import job_store

logger = logging.getLogger("drishya.main")
configure_logging()

app = FastAPI(title=settings.app_name, version="2.1.0")

# CORS config
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include specs and prompts routes
app.include_router(archive.router)
app.include_router(chat.router)




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


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "service": settings.service_name,
        "environment": settings.environment,
        "database": "configured" if settings.database_url else "missing",
        "redis": settings.redis_url,
    }


@app.get("/metrics")
async def metrics_endpoint():
    return PlainTextResponse(metrics.render_prometheus(), media_type="text/plain; version=0.0.4")

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        dead_connections = []
        for connection in self.active_connections:
            try:
                await connection.send_text(json.dumps(message))
            except Exception:
                dead_connections.append(connection)
        for dead in dead_connections:
            self.disconnect(dead)

manager = ConnectionManager()

async def _handle_websocket_connection(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
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
        import redis.asyncio as aioredis
        redis_conn = aioredis.from_url(settings.redis_url, decode_responses=True)
        await redis_conn.publish("live_stream", json.dumps(payload, default=str))
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
}


def country_name_from_code(country_code: str) -> str:
    return COUNTRY_NAMES_BY_CODE.get((country_code or "").upper(), country_code or "Global")


def country_coords_from_code(country_code: str) -> Optional[dict]:
    return COUNTRY_COORDS.get(country_name_from_code(country_code))


def article_to_world_alert(article: Article) -> Optional[dict]:
    coords = country_coords_from_code(article.country_code)
    if not coords:
        return None

    return {
        "id": article.id,
        "location": country_name_from_code(article.country_code),
        "lat": coords["lat"],
        "lon": coords["lon"],
        "severity": "high" if article.impact_level == "High Impact" else "medium",
        "headline": article.title,
        "source": article.source or "OSINT Mesh",
        "url": article.url,
        "timestamp": article.published_at.isoformat(),
        "summary": article.summary or article.headline or "Details restricted to Stratcom command.",
    }

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
    logger.info("[Main] Starting background ingestion loop for all configured countries.")
    while True:
        try:
            result = await run_ingestion_cycle(test_mode=False)
            logger.info(
                "[Main] Background ingestion step completed: raw=%s processed=%s high_impact=%s",
                result["raw_articles"],
                result["processed"],
                result["high_impact"],
            )
        except Exception as e:
            logger.error(f"[Main] Ingestion error: {e}")

        await asyncio.sleep(60)

async def redis_listener_task():
    """
    Subscribes to Redis 'live_stream' channel and broadcasts received articles
    to all websocket clients connected to this node.
    Handles automatic reconnection and falls back to local memory_stream if Redis is offline.
    """
    import redis.asyncio as aioredis
    import json
    
    redis_online = False
    
    while True:
        try:
            logger.info("[Main] Attempting connection to Redis Pub/Sub...")
            redis_conn = aioredis.from_url(settings.redis_url, socket_timeout=5.0)
            # Test connection
            await redis_conn.ping()
            
            pubsub = redis_conn.pubsub()
            await pubsub.subscribe("live_stream")
            logger.info("[Main] Redis pub/sub successfully subscribed to channel 'live_stream'.")
            redis_online = True
            
            async for message in pubsub.listen():
                if message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        await manager.broadcast({
                            "type": "live_article",
                            "article": data
                        })
                    except Exception as ex:
                        logger.error(f"[Main] Error parsing pubsub message: {ex}")
        except Exception as e:
            logger.warning(f"[Main] Redis pub/sub error/offline: {e}.")
            if not redis_online:
                logger.info("[Main] Falling back to local in-memory event stream.")
                # Fallback to local memory stream
                def on_live_signal(article):
                    asyncio.create_task(manager.broadcast({
                        "type": "live_article",
                        "article": article
                    }))
                memory_stream.subscribe(on_live_signal)
                break
            else:
                logger.info("[Main] Retrying Redis Pub/Sub connection in 5 seconds...")
                await asyncio.sleep(5)

@app.on_event("startup")
async def startup_event():
    # Run DB initializations
    await create_tables()
    # Start Redis listener for pub/sub live stream broadcasting.
    asyncio.create_task(redis_listener_task())
    # Start periodic ingestion to keep the local news mesh updated automatically.
    asyncio.create_task(periodic_ingestion_loop())
    logger.info("[Main] API startup complete; background ingestion and redis listener started.")

@app.get("/api/news/all")
async def get_all_news(
    category: str = Query("All"),
    timeframe: str = Query("24h"),
    db: AsyncSession = Depends(get_db)
):
    """
    Simulates /api/news/all returning country dossier arrays formatted for App.tsx
    """
    # Query past 30 days from database to ensure high-density news feeds
    now = datetime_now()
    cutoff = now - timedelta_now(days=30)
    
    # Query database articles (High Impact)
    stmt = select(Article).where(Article.published_at >= cutoff)
    stmt = stmt.order_by(Article.published_at.desc())
    result = await db.execute(stmt)
    db_articles = result.scalars().all()

    def matches_category(article: Article, selected_category: str) -> bool:
        article_category = get_frontend_category(article.department, article.title, article.source)
        if selected_category == "All":
            return True
        return article_category == selected_category

    # Build dossiers map
    countries = [
        "China", "Pakistan", "Afghanistan", "Bangladesh", 
        "Myanmar", "Nepal", "Bhutan", "Sri Lanka", "Maldives"
    ]
    
    country_meta = {
        "China": {"region": "Northern Front", "threatLevel": "Critical"},
        "Pakistan": {"region": "Western Front", "threatLevel": "High"},
        "Afghanistan": {"region": "Western Front", "threatLevel": "High"},
        "Bangladesh": {"region": "Eastern Front", "threatLevel": "Moderate"},
        "Myanmar": {"region": "Southeastern Front", "threatLevel": "Critical"},
        "Nepal": {"region": "Northern Front", "threatLevel": "Moderate"},
        "Bhutan": {"region": "Northern Front", "threatLevel": "Moderate"},
        "Sri Lanka": {"region": "Indian Ocean", "threatLevel": "Moderate"},
        "Maldives": {"region": "Indian Ocean", "threatLevel": "Moderate"}
    }

    results = {}
    for country in countries:
        meta = country_meta[country]
        
        # Filter matching signals
        filtered_articles = [
            article for article in db_articles
            if article.country_code.upper() == get_country_code(country)
            or article.title.lower().find(country.lower()) >= 0
        ]
        if category != "All":
            filtered_articles = [article for article in filtered_articles if matches_category(article, category)]

        signals = [
            {
                "id": art.id,
                "country": country,
                "category": get_frontend_category(art.department, art.title, art.source),
                "impact": "High" if art.impact_level == "High Impact" else ("Medium" if art.impact_level == "Medium Impact" else "Low"),
                "headline": art.title,
                "summary": art.summary or art.content[:150],
                "source": art.source or "OSINT Mesh",
                "timestamp": art.published_at.isoformat(),
                "url": art.url,
                "verification_status": "Verified Source",
                "confidence_score": 0.98,
            }
            for art in sorted(filtered_articles, key=lambda item: item.published_at, reverse=True)
        ]

        operational_summary = (
            "STATUS: STABLE // NO NEW SIGNAL IN DETECTED WINDOW"
            if not signals else
            f"Ingestion mesh verified. Detected {len(signals)} tactical and strategic signals in the selected window."
        )

        results[country] = {
            "region": meta["region"],
            "threat_level": meta["threatLevel"],
            "last_synced": now.isoformat(),
            "operational_summary": operational_summary,
            "signals": signals,
            "source_status": "normal"
        }

    return results


@app.get("/api/news/country")
async def get_specific_country_news(
    name: str = Query(...),
    code: str = Query(...),
    db: AsyncSession = Depends(get_db)
):
    name = name.strip()
    code = code.strip().upper()
    
    # Register/override the country name in ISO_COUNTRIES mapping
    from backend.app.services.ingestion import ISO_COUNTRIES, fetch_country_news
    from backend.app.services.classifier import classify_and_store_batch, ImpactClassifier
    import httpx
    import hashlib
    from datetime import datetime
    
    ISO_COUNTRIES[code] = name
    
    # 1. Query database for articles matching the country code
    stmt = select(Article).where(Article.country_code == code).order_by(Article.published_at.desc()).limit(50)
    res = await db.execute(stmt)
    db_articles = res.scalars().all()
    
    # 2. Trigger fresh fetch if db has fewer than 15 articles or latest is older than 15 mins
    from datetime import timezone
    is_stale = True
    if db_articles:
        latest_art = db_articles[0]
        now = datetime.now(timezone.utc) if latest_art.published_at.tzinfo is not None else datetime.utcnow()
        time_diff = (now - latest_art.published_at).total_seconds()
        if time_diff < 900 and len(db_articles) >= 15:
            is_stale = False
            
    fetched_signals = []
    if is_stale:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            try:
                raw_articles = await fetch_country_news(client, code, budget=50)
                if raw_articles:
                    # Save to DB (this handles duplicates and only saves High/Medium Impact)
                    await classify_and_store_batch(db, raw_articles)
                    
                    # Also classify them for our immediate response
                    classifier = ImpactClassifier()
                    for art in raw_articles:
                        impact, dept = classifier.classify(art.get("title", ""), art.get("content", ""))
                        fetched_signals.append({
                            "id": f"dyn-{hashlib.md5(art['url'].encode()).hexdigest()}",
                            "country": name,
                            "category": get_frontend_category(dept, art.get("title", ""), art.get("source", "")),
                            "impact": "High" if impact == "High Impact" else ("Medium" if impact == "Medium Impact" else "Low"),
                            "headline": art.get("title", "Untitled"),
                            "summary": art.get("summary") or art.get("content", "")[:150],
                            "source": art.get("source") or "OSINT Feed",
                            "timestamp": art.get("published_at").isoformat() if isinstance(art.get("published_at"), datetime) else str(art.get("published_at")),
                            "url": art["url"],
                            "verification_status": "Verified Source",
                            "confidence_score": 0.98,
                        })
            except Exception as e:
                logger.error(f"Error fetching real-time news for {name} ({code}): {e}")
                
    # 3. Format database articles
    db_signals = [
        {
            "id": art.id,
            "country": name,
            "category": get_frontend_category(art.department, art.title, art.source),
            "impact": "High" if art.impact_level == "High Impact" else ("Medium" if art.impact_level == "Medium Impact" else "Low"),
            "headline": art.title,
            "summary": art.summary or art.content[:150],
            "source": art.source or "OSINT Mesh",
            "timestamp": art.published_at.isoformat(),
            "url": art.url,
            "verification_status": "Verified Source",
            "confidence_score": 0.98,
        }
        for art in db_articles
    ]
    
    # 4. Merge database and fetched signals, keeping unique URLs
    all_signals = []
    seen_urls = set()
    for sig in db_signals + fetched_signals:
        if sig["url"] not in seen_urls:
            seen_urls.add(sig["url"])
            all_signals.append(sig)
            
    all_signals = all_signals[:50]
    
    threat_level = "Moderate"
    if all_signals:
        if any(s["impact"] == "High" for s in all_signals):
            threat_level = "Critical"
        else:
            threat_level = "High"
            
    now = datetime_now()
    return {
        "region": "Global Sector",
        "threat_level": threat_level,
        "last_synced": now.isoformat(),
        "operational_summary": f"Ingestion mesh verified. Detected {len(all_signals)} tactical signals for {name}." if all_signals else "STATUS: STABLE // NO NEW SIGNAL IN DETECTED WINDOW",
        "signals": all_signals,
        "source_status": "normal"
    }


@app.get("/api/news/stream")
async def sse_news_stream():
    """
    SSE stream endpoint broadcasting events.
    """
    async def event_generator():
        yield f"data: {json.dumps({'type': 'connected'})}\n\n"
        while True:
            # Keep-alive ping
            await asyncio.sleep(20)
            yield f"data: {json.dumps({'type': 'ping'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/world/alerts")
async def world_alerts_endpoint(
    force: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    del force
    now = datetime_now()
    cutoff = now - timedelta_now(days=7)

    stmt = select(Article).where(Article.published_at >= cutoff).order_by(Article.published_at.desc()).limit(150)
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
        if len(alerts) >= 20:
            break

    return {
        "updatedAt": now.isoformat(),
        "count": len(alerts),
        "alerts": alerts,
    }


@app.post("/api/news/refresh")
async def refresh_news_endpoint():
    try:
        result = await run_ingestion_cycle(test_mode=False)
        return {"success": True, **result}
    except Exception as exc:
        logger.error("[Main] Manual refresh failed: %s", exc)
        raise HTTPException(status_code=500, detail="News refresh failed")

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

# Scrape Job Worker Simulation for testing
async def run_mock_scrape_job(job_id: str, url: str, platform: str):
    try:
        await job_store.update(job_id, "scrape", status="scraping", progress=20, step="scraping")
        await asyncio.sleep(2)
        await job_store.update(job_id, "scrape", status="summarizing", progress=50, step="summarizing")
        await asyncio.sleep(2)
        await job_store.update(job_id, "scrape", status="saving", progress=80, step="saving")
        await asyncio.sleep(1)
        
        # Simulate finalized article output
        result_payload = {
            "title": f"Strategic Analysis of {url.split('//')[-1].split('/')[0]}",
            "url": url,
            "source": "CRAWLER",
            "country_code": "Global",
            "platform": platform,
        }
        await job_store.update(job_id, "scrape", status="completed", progress=100, step="completed", result=result_payload)
    except Exception as e:
        await job_store.update(job_id, "scrape", status="failed", progress=100, step="failed", error=str(e))

# Utility helper mapping functions
def datetime_now() -> Any:
    from datetime import datetime
    return datetime.utcnow()

def timedelta_now(**kwargs) -> Any:
    from datetime import timedelta
    return timedelta(**kwargs)

def get_country_code(country: str) -> str:
    mapping = {
        "China": "CN", "Pakistan": "PK", "Afghanistan": "AF", "Bangladesh": "BD",
        "Myanmar": "MM", "Nepal": "NP", "Bhutan": "BT", "Sri Lanka": "LK", "Maldives": "MV"
    }
    return mapping.get(country, "GL")

def get_frontend_category(dept: str, title: str = "", source: str = "") -> str:
    mapping = {
        "Military & Defense": "Military",
        "Economic & Financial": "Economic",
        "Social Affairs & Welfare": "Social",
        "Political & Diplomatic": "Political"
    }
    base_category = mapping.get(dept, "Political")
    tech_keywords = re.compile(r"\b(cyber|drone|uav|satellite|radar|surveillance|sensor|telecom|internet|ai|artificial intelligence|machine learning|ml|technology|tech|space|communications|signal|gps)\b", re.I)
    combined_text = f"{title or ''} {source or ''}"
    if base_category in {"Military", "Economic"} and tech_keywords.search(combined_text):
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
    sources = [{"name": art.source or "OSINT Feed", "url": art.url} for art in top_matches]
    
    # 2. Formulate answer using OpenAI/Gemini if available, or template
    summary = ""
    if top_matches:
        article_context = "\n".join([f"Source: {art.source}\nTitle: {art.title}\nContent: {art.content[:250]}\n" for art in top_matches])
        prompt = (
            f"You are a strategic intelligence assistant. Answer the user query: '{query}' "
            f"directly and concisely based on these news articles. Cite the sources where appropriate:\n\n{article_context}"
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
                f"Extracted Briefing regarding '{query}' based on {len(top_matches)} matching reports:\n\n" + 
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

# Serve the built frontend assets if available.
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "dist"
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
    logger.info("[Main] Mounted frontend static assets from %s", FRONTEND_DIST)

    @app.get("/{full_path:path}")
    async def serve_single_page_app(full_path: str):
        return FileResponse(FRONTEND_DIST / "index.html")
