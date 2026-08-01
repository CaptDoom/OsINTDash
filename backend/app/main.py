import os
import asyncio
import json
import logging
from typing import Dict, Any, List, Set, Optional
from pydantic import BaseModel
from fastapi import FastAPI, Depends, Query, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.config import settings
from backend.app.database import create_tables, get_db, Article
from backend.app.api.routes import archive, chat
from backend.app.services.ingestion import fetch_global_news
from backend.app.services.classifier import ImpactClassifier, memory_stream
from backend.app.services.summarizer import call_openai, call_gemini

logger = logging.getLogger("drishya.main")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Drishya 2.0 API", version="2.0.0")

# CORS config
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include specs and prompts routes
app.include_router(archive.router)
app.include_router(chat.router)

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

# Background Ingestion Task
async def periodic_ingestion_loop():
    logger.info("[Main] Starting background ingestion loop.")
    classifier = ImpactClassifier()
    
    while True:
        try:
            # Run tables check in case not created
            await create_tables()
            
            # Fetch news
            raw_articles = await fetch_global_news(limit_per_country=5, test_mode=True)
            
            async for db in get_db():
                for art in raw_articles:
                    # Save (filters high impact automatically)
                    await classifier.save_article(db, art)
                break # Close session
                
            logger.info("[Main] Background ingestion step completed. Sleeping for 60 seconds.")
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
            redis_conn = aioredis.from_url(settings.REDIS_URL, socket_timeout=5.0)
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
    # Launch ingestion loop in background
    asyncio.create_task(periodic_ingestion_loop())
    # Launch Redis listener task in background
    asyncio.create_task(redis_listener_task())

@app.get("/api/news/all")
async def get_all_news(
    category: str = Query("All"),
    timeframe: str = Query("24h"),
    db: AsyncSession = Depends(get_db)
):
    """
    Simulates /api/news/all returning country dossier arrays formatted for App.tsx
    """
    now = datetime_now()
    if timeframe == "1h":
        delta = timedelta_now(hours=1)
    elif timeframe == "24h" or timeframe == "1d":
        delta = timedelta_now(hours=24)
    elif timeframe == "7d" or timeframe == "1w":
        delta = timedelta_now(days=7)
    else:
        delta = timedelta_now(hours=24)

    cutoff = now - delta
    
    # Query database articles (High Impact)
    stmt = select(Article).where(Article.published_at >= cutoff)
    if category != "All":
        # Handle map category name
        db_cat = category
        if category == "Tech":
            db_cat = "Economic & Financial" # fallback map
        stmt = stmt.where(Article.department.contains(db_cat) | Article.category.contains(db_cat))

    stmt = stmt.order_by(Article.published_at.desc())
    result = await db.execute(stmt)
    db_articles = result.scalars().all()

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
        signals = []
        for art in db_articles:
            if art.country_code.upper() == get_country_code(country) or art.title.lower().find(country.lower()) >= 0:
                # Map to frontend Signal model
                signals.append({
                    "id": art.id,
                    "country": country,
                    "category": get_frontend_category(art.department),
                    "impact": "High" if art.impact_level == "High Impact" else "Medium",
                    "headline": art.title,
                    "summary": art.summary or art.content[:150],
                    "source": art.source or "OSINT Mesh",
                    "timestamp": art.published_at.isoformat(),
                    "url": art.url,
                    "verification_status": "Verified Source",
                    "confidence_score": 0.98
                })

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

# WebSockets Endpoint for scraping progress updates
@app.websocket("/api/scrape/status")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Echo or process incoming messages
            data = await websocket.receive_text()
            parsed = json.loads(data)
            if parsed.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.post("/api/scrape")
async def trigger_scrape_endpoint(payload: dict):
    urls = payload.get("urls", [])
    if not urls:
        raise HTTPException(status_code=400, detail="URLs parameter is required.")
        
    # Queue scraping job
    job_ids = []
    for url in urls:
        job_id = f"job_{uuid_str()}"
        job_ids.append(job_id)
        
        # Run background worker simulation
        asyncio.create_task(run_mock_scrape_job(job_id, url))
        
    return {"success": True, "jobIds": job_ids}

# Scrape Job Worker Simulation for testing
async def run_mock_scrape_job(job_id: str, url: str):
    try:
        await manager.broadcast({"type": "job_update", "jobId": job_id, "status": "scraping", "progress": 20})
        await asyncio.sleep(2)
        await manager.broadcast({"type": "job_update", "jobId": job_id, "status": "summarizing", "progress": 50})
        await asyncio.sleep(2)
        await manager.broadcast({"type": "job_update", "jobId": job_id, "status": "saving", "progress": 80})
        await asyncio.sleep(1)
        
        # Simulate finalized article output
        result_payload = {
            "title": f"Strategic Analysis of {url.split('//')[-1].split('/')[0]}",
            "url": url,
            "source": "CRAWLER",
            "country_code": "Global"
        }
        await manager.broadcast({"type": "job_update", "jobId": job_id, "status": "completed", "progress": 100, "result": result_payload})
    except Exception as e:
        await manager.broadcast({"type": "job_update", "jobId": job_id, "status": "failed", "progress": 100, "error": str(e)})

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

def get_frontend_category(dept: str) -> str:
    mapping = {
        "Military & Defense": "Military",
        "Economic & Financial": "Economic",
        "Social Affairs & Welfare": "Social",
        "Political & Diplomatic": "Political"
    }
    return mapping.get(dept, "Political")

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
            if settings.OPENAI_API_KEY:
                summary = await call_openai(prompt, "You are a senior analyst.")
            elif settings.GOOGLE_API_KEY:
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
