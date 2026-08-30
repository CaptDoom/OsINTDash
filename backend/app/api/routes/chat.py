import os
import re
import hashlib
import logging
import math
import asyncio
from collections import Counter
from pathlib import Path
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from backend.app.database import get_db, Article
from backend.app.services.summarizer import call_openai, call_gemini, call_ollama
from backend.app.config import settings
from backend.app.services.job_store import job_store
from backend.app.redis_pool import cache_get, cache_set

try:
    from pgvector.sqlalchemy import cosine_distance
    HAS_PGVECTOR = True
except ImportError:
    HAS_PGVECTOR = False

RE_TOKENIZER = re.compile(r"[A-Za-z0-9']{3,}")

def re_split(text: str) -> List[str]:
    return RE_TOKENIZER.findall(text)

logger = logging.getLogger("drishya.chat")

router = APIRouter(prefix="/api/chat")

class FusionResponse(BaseModel):
    summary: str
    relevant_articles: List[Dict[str, Any]]

# Simple TF-IDF/BM25 & Cosine Similarity fallback in pure Python
def get_text_overlap_similarity(doc_text: str, article_text: str) -> float:
    doc_words = set(re_split(doc_text.lower()))
    art_words = set(re_split(article_text.lower()))
    if not doc_words or not art_words:
        return 0.0
    intersection = doc_words.intersection(art_words)
    return len(intersection) / math.sqrt(len(doc_words) * len(art_words))

def normalize_tokens(text: str) -> List[str]:
    tokens = [token.lower() for token in re_split(text) if len(token) > 2]
    return [token for token in tokens if not token.isdigit()]


def build_term_vector(text: str) -> Counter[str]:
    return Counter(normalize_tokens(text))


def score_article_similarity(doc_text: str, article: Article) -> float:
    doc_vector = build_term_vector(doc_text)
    art_vector = build_term_vector(f"{article.title} {article.summary or article.content}")
    if not doc_vector or not art_vector:
        return 0.0

    common_terms = set(doc_vector) & set(art_vector)
    dot_product = sum(doc_vector[token] * art_vector[token] for token in common_terms)
    norm_a = math.sqrt(sum(value**2 for value in doc_vector.values()))
    norm_b = math.sqrt(sum(value**2 for value in art_vector.values()))
    base_score = dot_product / (norm_a * norm_b) if norm_a and norm_b else 0.0

    # Boost score based on country matching to solve vocabulary mismatch
    boost = 0.0
    query_lower = doc_text.lower()
    
    country_terms = {
        "CN": ["china", "chinese", "peking", "beijing"],
        "PK": ["pakistan", "pakistani", "islamabad"],
        "AF": ["afghanistan", "afghan", "kabul"],
        "BD": ["bangladesh", "bangladeshi", "dhaka"],
        "MM": ["myanmar", "burma", "burmese", "naypyidaw"],
        "NP": ["nepal", "nepalese", "nepali", "kathmandu"],
        "BT": ["bhutan", "bhutanes", "thimphu"],
        "LK": ["sri lanka", "lankan", "colombo"],
        "MV": ["maldives", "maldivian", "male"],
        "IN": ["india", "indian", "delhi", "new delhi"],
        "US": ["united states", "usa", "us", "american", "washington"],
        "RU": ["russia", "russian", "moscow"],
        "IR": ["iran", "iranian", "tehran"],
        "IL": ["israel", "israeli", "jerusalem"],
        "TW": ["taiwan", "taiwanese", "taipei"],
        "JP": ["japan", "japanese", "tokyo"],
        "UA": ["ukraine", "ukrainian", "kyiv"]
    }
    
    art_country = (article.country_code or "").upper()
    art_text = f"{article.title} {article.summary or ''} {article.content or ''}".lower()
    
    for code, terms in country_terms.items():
        if any(term in query_lower for term in terms):
            if art_country == code or any(term in art_text for term in terms):
                boost += 0.4
                if art_country == code:
                    boost += 0.2  # Direct country tag match boost

    # Boost high-impact or high-confidence reports slightly for precision
    if getattr(article, 'impact_level', '') == 'High Impact':
        boost += 0.1
    
    # Source reputation boost: verified sources get higher ranking
    try:
        from backend.app.services.credibility import compute_source_reputation_score
        rep_score = compute_source_reputation_score(getattr(article, 'source', None))
        boost += rep_score * 0.15  # Max +0.15 for tier-1 sources
    except Exception:
        pass
    
    # Recency boost: newer articles score higher
    from datetime import datetime, timezone
    pub = getattr(article, 'published_at', None)
    if pub:
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - pub).total_seconds() / 3600
        if age_hours < 24:
            boost += 0.15
        elif age_hours < 72:
            boost += 0.08
        elif age_hours < 168:
            boost += 0.03
    
    return base_score + boost


async def search_relevant_articles(db: AsyncSession, doc_text: str, limit: int = 5) -> List[Article]:
    from backend.app.services.classifier import get_transformer
    import numpy as np
    
    # Fetch a larger window of recent articles to ensure we find matches across countries
    stmt = select(Article).order_by(Article.published_at.desc()).limit(500)
    result = await db.execute(stmt)
    articles = result.scalars().all()
    if not articles:
        return []

    # 1. Try vector-based search
    try:
        transformer = get_transformer()
        query_vector = transformer.encode(doc_text, convert_to_numpy=True)
        
        if db.bind.dialect.name == "postgresql" and HAS_PGVECTOR:
            try:
                stmt_pg = select(Article).order_by(cosine_distance(Article.embedding, query_vector.tolist())).limit(limit)
                result_pg = await db.execute(stmt_pg)
                return list(result_pg.scalars().all())
            except Exception as pg_err:
                logger.warning(f"[Chat] pgvector query failed, falling back to local: {pg_err}")

        # Local numpy similarity fallback using query vector
        scored_articles = []
        query_norm = np.linalg.norm(query_vector)
        if query_norm > 0:
            query_vec_norm = query_vector / query_norm
            for art in articles:
                embedding_val = art.embedding
                if not embedding_val:
                    continue
                    
                if isinstance(embedding_val, str):
                    try:
                        import json
                        vec_list = json.loads(embedding_val)
                    except Exception:
                        continue
                elif isinstance(embedding_val, (list, tuple)):
                    vec_list = embedding_val
                else:
                    try:
                        vec_list = list(embedding_val)
                    except Exception:
                        continue
                        
                if len(vec_list) != len(query_vector):
                    continue
                    
                art_vec = np.array(vec_list, dtype=np.float32)
                art_norm = np.linalg.norm(art_vec)
                if art_norm <= 0:
                    continue
                    
                art_vec_norm = art_vec / art_norm
                similarity = float(np.dot(query_vec_norm, art_vec_norm))
                scored_articles.append((similarity, art))
                
        if scored_articles:
            scored_articles.sort(key=lambda x: x[0], reverse=True)
            return [art for _, art in scored_articles[:limit]]
            
    except Exception as e:
        logger.warning(f"[Chat] Embedding search failed or skipped: {e}")

    # 2. Fallback to pure Python TF-IDF/Term cosine similarity (always works!)
    logger.info("[Chat] Falling back to text-overlap cosine similarity search.")
    scored_articles = []
    for art in articles:
        score = score_article_similarity(doc_text, art)
        if score > 0:
            scored_articles.append((score, art))
            
    if not scored_articles:
        # Absolutely no match, return latest articles
        return sorted(articles, key=lambda x: x.published_at, reverse=True)[:limit]

    scored_articles.sort(key=lambda x: x[0], reverse=True)
    return [art for _, art in scored_articles[:limit]]

MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # 15 MB
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt", ".md"}


@router.post("/fusion")
async def upload_and_fuse_document(
    request: Request,
    file: UploadFile = File(...),
    instructions: Optional[str] = Form(None)
):
    """
    Accepts a document upload with optional instruction prompts, stores it for background processing,
    and returns a job id immediately. The status endpoint exposes progress.
    """
    from backend.app.services.net_safety import sanitize_filename, is_allowed_upload_extension

    # Path traversal prevention: strip directory components from filename
    safe_name = sanitize_filename(file.filename or "upload")
    if not is_allowed_upload_extension(safe_name, ALLOWED_UPLOAD_EXTENSIONS):
        raise HTTPException(status_code=400, detail=f"Unsupported file type. Allowed: {', '.join(ALLOWED_UPLOAD_EXTENSIONS)}")

    # Pre-check Content-Length header (advisory, attacker can spoof)
    declared_len = request.headers.get("content-length")
    if declared_len and int(declared_len) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 15MB limit.")

    temp_dir = Path(__file__).resolve().parents[3] / "scratch" / "uploads"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_file_path = temp_dir / f"uploaded_{uuid_str()}_{safe_name}"

    # Chunked read with running-total cap — no single allocation exceeds MAX_UPLOAD_BYTES
    size = 0
    try:
        with open(temp_file_path, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    out.close()
                    temp_file_path.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="File exceeds 15MB limit.")
                out.write(chunk)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {exc}")

    job = await job_store.create(
        "fusion",
        {
            "filename": file.filename,
            "file_path": str(temp_file_path),
            "content_type": file.content_type,
            "instructions": instructions or "",
        },
    )

    if settings.enable_inline_job_processing:
        asyncio.create_task(process_fusion_job(job.job_id, str(temp_file_path), file.filename, instructions))

    return {"job_id": job.job_id, "status": job.status, "progress": job.progress}


@router.get("/fusion/status/{job_id}")
async def fusion_job_status(job_id: str):
    job = await job_store.get("fusion", job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Fusion job not found")
    return job.__dict__


async def process_fusion_job(job_id: str, temp_file_path: str, filename: str, instructions: Optional[str] = None):
    try:
        await job_store.update(job_id, "fusion", status="parsing", progress=15, step="parsing")
        extracted_text = ""

        try:
            from docling.document_converter import DocumentConverter

            logger.info("[RAG] Parsing %s using IBM Docling...", filename)
            converter = DocumentConverter()
            result = converter.convert(temp_file_path)
            extracted_text = result.document.export_to_markdown()
        except Exception as docling_err:
            logger.warning("[RAG] Docling parsing failed for %s: %s", filename, docling_err)
            ext = filename.lower().split('.')[-1]
            if ext == "pdf":
                extracted_text = extract_simple_pdf_text(temp_file_path)
            elif ext in ["docx", "doc"]:
                extracted_text = extract_simple_docx_text(temp_file_path)
            else:
                try:
                    with open(temp_file_path, "r", encoding="utf-8", errors="ignore") as handle:
                        extracted_text = handle.read()
                except Exception as read_err:
                    extracted_text = f"Fallback raw reader error: {read_err}"

        if not extracted_text.strip():
            extracted_text = "Empty document payload or unreadable file format."

        await job_store.update(job_id, "fusion", status="searching", progress=45, step="searching")
        matching_articles: List[Article] = []
        async for session in get_db():
            matching_articles = await search_relevant_articles(session, extracted_text, limit=settings.fusion_top_k)
            break

        article_context = ""
        relevant_list = []
        for idx, art in enumerate(matching_articles):
            article_context += (
                f"SOURCE [{idx+1}]: {art.title}\n"
                f"URL: {art.url}\n"
                f"Country: {art.country_code}\n"
                f"Source: {art.source}\n"
                f"Summary: {art.summary or art.content[:180]}\n\n"
            )
            relevant_list.append(
                {
                    "id": art.id,
                    "title": art.title,
                    "url": art.url,
                    "source": art.source,
                    "department": art.department,
                    "country_code": art.country_code,
                    "summary": art.summary or art.content[:180],
                    "published_at": art.published_at.isoformat(),
                }
            )

        await job_store.update(job_id, "fusion", status="synthesizing", progress=75, step="synthesizing")
        # 2. LLM synthesis - crisp, concise, jargon-free
        prompt = f"""
        Cross-reference the uploaded document with the news reports below.
        Write in plain, everyday English. No military or intelligence jargon.
        Be direct and concise. Every sentence must add new information.
        
        UPLOADED DOCUMENT:
        {extracted_text[:3000]}
        
        NEWS REPORTS:
        {article_context}
        
        USER REQUEST: {instructions if instructions else "Provide a general comparison."}
        
        FORMAT:
        **Document Summary** - 2-3 sentences on what the document is about.
        **News Comparison** - How the document connects to recent news. Cite sources as [Title](URL).
        **Impact** - What this means for everyday people in 1-2 sentences.
        
        RULES:
        - No jargon (no OSINT, telemetry, bilaterals, tactical, reconnaissance, frontier, strategic, etc.).
        - No filler words, no first-person, no opinions.
        - If no connection exists, say: "No link found between the document and current news."
        - Keep the total response under 300 words.
        """

        fused_summary = ""
        if settings.llm_provider == "ollama" and settings.ollama_base_url:
            try:
                fused_summary = await call_ollama(prompt, "You are a clear and simple writer.")
            except Exception as exc:
                logger.warning("[Chat] Ollama call failed, falling back: %s", exc)
        
        if not fused_summary:
            if settings.openai_api_key:
                fused_summary = await call_openai(prompt, "You are a clear and simple writer.")
            elif settings.google_api_key:
                fused_summary = await call_gemini(prompt, "You are a clear and simple writer.")
            else:
                fused_summary = generate_local_fusion_fallback(extracted_text, matching_articles)

        await job_store.update(
            job_id,
            "fusion",
            status="completed",
            progress=100,
            step="completed",
            result={"summary": fused_summary, "relevant_articles": relevant_list},
        )
    except Exception as exc:
        logger.error("[RAG] Fusion job failed: %s", exc)
        await job_store.update(job_id, "fusion", status="failed", progress=100, step="failed", error=str(exc))
    finally:
        try:
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)
        except Exception:
            pass

def uuid_str() -> str:
    import uuid
    return str(uuid.uuid4())[:8]

def extract_simple_pdf_text(path: str) -> str:
    # Safe text reader fallback
    try:
        import pypdf
        reader = pypdf.PdfReader(path)
        text = ""
        for page in reader.pages:
            text += page.extract_text() + "\n"
        return text
    except Exception:
        # Fallback to shell string extractor if package is absent
        with open(path, "rb") as f:
            content = f.read()
            # extract basic ascii strings
            import re
            return " ".join(re.findall(rb'[a-zA-Z0-9\s\.\,\;\-\:\?]{4,}', content).decode('ascii', errors='ignore'))

def extract_simple_docx_text(path: str) -> str:
    try:
        import docx
        doc = docx.Document(path)
        return "\n".join([p.text for p in doc.paragraphs])
    except Exception:
        return "Word document text extraction fallback"

def generate_local_fusion_fallback(doc_text: str, articles: List[Article]) -> str:
    md = "**News Briefing**\n\n"
    
    md += "**What was searched:**\n"
    md += f"> {doc_text[:300]}...\n\n"
    
    if not articles:
        md += "No matching news articles found in the database.\n"
    else:
        md += f"**Found {len(articles)} related articles:**\n\n"
        for art in articles[:5]:
            md += (
                f"- **[{art.title}]({art.url or '#'})**\n"
                f"  Source: {art.source or 'Unknown'} | {art.country_code or 'Global'}\n"
                f"  {art.summary or art.content[:160]}\n\n"
            )
    
    md += "**What this means:** Nothing unusual. Daily life and travel continue normally. Check back for updates.\n"
    return md


class ChatMessage(BaseModel):
    sender: str
    text: str

class ChatQuery(BaseModel):
    query: str
    history: Optional[List[ChatMessage]] = None


@router.post("/query")
async def chat_query(payload: ChatQuery):
    query_text = payload.query
    if not query_text.strip():
        raise HTTPException(status_code=400, detail="Query text cannot be empty.")

    # Check Redis cache for frequent queries (10-minute TTL)
    query_hash = hashlib.md5(query_text.lower().strip().encode()).hexdigest()
    cache_key = f"drishya:chat:query:{query_hash}"
    cached = await cache_get(cache_key)
    if cached:
        logger.info("[Chat] Cache hit for query: %s", query_text[:50])
        return cached

    # 1. Search relevant articles
    matching_articles: List[Article] = []
    async for session in get_db():
        matching_articles = await search_relevant_articles(session, query_text, limit=settings.fusion_top_k)
        break

    article_context = ""
    relevant_list = []
    for idx, art in enumerate(matching_articles):
        article_context += (
            f"SOURCE [{idx+1}]: {art.title}\n"
            f"URL: {art.url}\n"
            f"Country: {art.country_code}\n"
            f"Source: {art.source}\n"
            f"Summary: {art.summary or art.content[:180]}\n\n"
        )
        relevant_list.append(
            {
                "id": art.id,
                "title": art.title,
                "url": art.url,
                "source": art.source,
                "department": art.department,
                "country_code": art.country_code,
                "summary": art.summary or art.content[:180],
                "published_at": art.published_at.isoformat(),
            }
        )

    # 2. Format history context
    history_context = ""
    if payload.history:
        history_context = "CONVERSATION HISTORY (Use this context to answer follow-up queries):\n"
        # Take up to the last 6 messages to prevent token bloat
        for msg in payload.history[-6:]:
            role = "Operator" if msg.sender == "user" else "Assistant"
            history_context += f"{role}: {msg.text}\n"
        history_context += "\n"

    # 3. LLM synthesis - crisp, concise, jargon-free
    prompt = f"""
    Answer the user's question using ONLY the news reports below.
    Write in plain, everyday English. No military or intelligence jargon.
    Be direct and concise. Every sentence must add new information.
    
    {history_context}
    
    USER QUESTION: {query_text}
    
    NEWS REPORTS:
    {article_context}
    
    FORMAT:
    **What Happened** - 2-3 sentences on the key facts.
    **Key Details** - Who, what, where, when. Cite sources as [Title](URL).
    **Impact** - What this means for everyday people in 1-2 sentences.
    
    RULES:
    - No jargon (no OSINT, telemetry, bilaterals, tactical, reconnaissance, frontier, strategic, etc.).
    - No filler words, no first-person, no opinions.
    - If no relevant reports exist, say: "No matching news found." and give a one-sentence safety note.
    - Keep the total response under 200 words.
    """

    fused_summary = ""
    if settings.llm_provider == "ollama" and settings.ollama_base_url:
        try:
            fused_summary = await call_ollama(prompt, "You are a clear and simple writer.")
        except Exception as exc:
            logger.warning("[Chat] Ollama call failed, falling back: %s", exc)
            
    if not fused_summary:
        if settings.openai_api_key:
            fused_summary = await call_openai(prompt, "You are a clear and simple writer.")
        elif settings.google_api_key:
            fused_summary = await call_gemini(prompt, "You are a clear and simple writer.")
        else:
            fused_summary = generate_local_fusion_fallback(query_text, matching_articles)

    response_data = {"summary": fused_summary, "relevant_articles": relevant_list}

    # Cache frequent queries for 10 minutes (skip rule-based fallbacks)
    if fused_summary and not fused_summary.startswith("**News Briefing"):
        await cache_set(cache_key, response_data, ttl=600)

    return response_data


# ─── Intent Detection ─────────────────────────────────────────────────────

def detect_query_intent(query: str) -> dict:
    """Analyze query to determine intent type and extract parameters."""
    q = query.lower().strip()
    intent = {"type": "general", "country": None, "timeframe": None, "department": None}

    # Country detection
    country_map = {
        "china": "CN", "chinese": "CN", "beijing": "CN",
        "pakistan": "PK", "pakistani": "PK", "islamabad": "PK",
        "india": "IN", "indian": "IN", "delhi": "IN",
        "afghanistan": "AF", "afghan": "AF", "kabul": "AF",
        "myanmar": "MM", "burma": "MM",
        "nepal": "NP", "nepalese": "NP",
        "bangladesh": "BD", "dhaka": "BD",
        "ukraine": "UA", "ukrainian": "UA", "kyiv": "UA",
        "russia": "RU", "russian": "RU", "moscow": "RU",
        "taiwan": "TW", "taiwanese": "TW",
        "iran": "IR", "iranian": "IR", "tehran": "IR",
        "israel": "IL", "israeli": "IL", "jerusalem": "IL",
        "japan": "JP", "japanese": "JP",
        "us": "US", "usa": "US", "united states": "US", "america": "US",
        "south korea": "KR", "korea": "KR", "pyongyang": "KP",
    }
    for term, code in country_map.items():
        if term in q:
            intent["country"] = code
            break

    # Timeframe detection
    if any(w in q for w in ["today", "right now", "current", "latest", "just now"]):
        intent["timeframe"] = "24h"
    elif any(w in q for w in ["this week", "past week", "last week", "7 days"]):
        intent["timeframe"] = "7d"
    elif any(w in q for w in ["this month", "past month", "last month", "30 days"]):
        intent["timeframe"] = "30d"

    # Intent type detection
    if any(w in q for w in ["risk", "threat", "danger", "safe", "safety"]):
        intent["type"] = "risk_assessment"
    elif any(w in q for w in ["trend", "change", "escalat", "increas", "decreas", "compar"]):
        intent["type"] = "trend_analysis"
    elif any(w in q for w in ["what happened", "summary", "briefing", "overview", "report"]):
        intent["type"] = "briefing"
    elif any(w in q for w in ["source", "credibl", "reliab", "verify", "trust"]):
        intent["type"] = "source_verification"
    elif any(w in q for w in ["forecast", "predict", "expect", "outlook", "likely"]):
        intent["type"] = "forecast"

    # Department detection
    dept_map = {
        "military": "Military & Defense", "defense": "Military & Defense",
        "army": "Military & Defense", "border": "Military & Defense",
        "economic": "Economic & Financial", "trade": "Economic & Financial",
        "finance": "Economic & Financial", "market": "Economic & Financial",
        "political": "Political & Diplomatic", "diplomatic": "Political & Diplomatic",
        "government": "Political & Diplomatic",
        "social": "Social Affairs & Welfare", "community": "Social Affairs & Welfare",
        "cyber": "Technology & Cyber", "technology": "Technology & Cyber",
        "hacking": "Technology & Cyber", "tech": "Technology & Cyber",
    }
    for term, dept in dept_map.items():
        if term in q:
            intent["department"] = dept
            break

    return intent


# ─── Streaming SSE Chat ───────────────────────────────────────────────────

@router.post("/stream")
async def chat_stream(payload: ChatQuery):
    """
    Streaming chat endpoint using Server-Sent Events.
    Returns articles immediately, then streams LLM tokens in real-time.
    """
    from fastapi.responses import StreamingResponse
    import json as _json

    query_text = payload.query
    if not query_text.strip():
        raise HTTPException(status_code=400, detail="Query text cannot be empty.")

    # Detect intent for enhanced prompting
    intent = detect_query_intent(query_text)

    # 1. Search relevant articles (with intent-aware filtering)
    matching_articles: List[Article] = []
    async for session in get_db():
        matching_articles = await search_relevant_articles(session, query_text, limit=settings.fusion_top_k)
        break

    # Filter by intent
    if intent["country"]:
        matching_articles = [a for a in matching_articles if (a.country_code or "").upper() == intent["country"]] or matching_articles
    if intent["department"]:
        matching_articles = [a for a in matching_articles if a.department == intent["department"]] or matching_articles

    # 2. Build article context
    article_context = ""
    relevant_list = []
    for idx, art in enumerate(matching_articles[:8]):
        article_context += (
            f"SOURCE [{idx+1}]: {art.title}\n"
            f"URL: {art.url}\n"
            f"Country: {art.country_code}\n"
            f"Department: {art.department}\n"
            f"Source: {art.source}\n"
            f"Impact: {art.impact_level}\n"
            f"Published: {art.published_at.isoformat()}\n"
            f"Summary: {art.summary or art.content[:200]}\n\n"
        )
        relevant_list.append({
            "id": art.id,
            "title": art.title,
            "url": art.url,
            "source": art.source,
            "department": art.department,
            "country_code": art.country_code,
            "impact_level": art.impact_level,
            "summary": art.summary or art.content[:200],
            "published_at": art.published_at.isoformat(),
        })

    # 3. Build context-aware prompt
    intent_hints = ""
    if intent["type"] == "risk_assessment":
        intent_hints = "Focus on safety, security, and risk factors. Highlight any threats or areas of concern."
    elif intent["type"] == "trend_analysis":
        intent_hints = "Compare current situation with recent past. Highlight what changed and in which direction."
    elif intent["type"] == "briefing":
        intent_hints = "Provide a structured executive briefing with key facts organized by sector."
    elif intent["type"] == "source_verification":
        intent_hints = "Discuss source credibility, cross-referencing status, and reliability of information."
    elif intent["type"] == "forecast":
        intent_hints = "Based on recent trends, provide an outlook. Note patterns and likely developments."

    history_context = ""
    if payload.history:
        history_context = "CONVERSATION HISTORY:\n"
        for msg in payload.history[-6:]:
            role = "User" if msg.sender == "user" else "Assistant"
            history_context += f"{role}: {msg.text}\n"
        history_context += "\n"

    prompt = f"""
You are an expert geopolitical analyst providing clear, actionable intelligence.
Write in plain, everyday English. No military or intelligence jargon.
Be direct and concise. Every sentence must add new information.

{history_context}
{intent_hints}

USER QUESTION: {query_text}

NEWS REPORTS:
{article_context}

FORMAT:
**What Happened** - 2-3 sentences on the key facts.
**Key Details** - Who, what, where, when. Cite sources as [Title](URL).
**Analysis** - Why this matters and what pattern it fits.
**Impact** - What this means for everyday people in 1-2 sentences.

RULES:
- No jargon (no OSINT, telemetry, bilaterals, tactical, reconnaissance, frontier, strategic, etc.).
- No filler words, no first-person, no opinions.
- If no relevant reports exist, say: "No matching news found." and give a one-sentence safety note.
- Keep the total response under 250 words.
"""

    # 4. Generate LLM response (streaming if OpenAI available, else standard)
    async def event_generator():
        # First: send article metadata as structured event
        yield f"event: articles\ndata: {_json.dumps({'articles': relevant_list, 'intent': intent})}\n\n"

        # Then: stream LLM text
        try:
            full_text = ""
            if settings.openai_api_key:
                from openai import AsyncOpenAI
                client = AsyncOpenAI(api_key=settings.openai_api_key)
                stream = await client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "You are a clear and simple writer providing geopolitical intelligence."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.2,
                    stream=True,
                )
                async for chunk in stream:
                    delta = chunk.choices[0].delta.content if chunk.choices else None
                    if delta:
                        full_text += delta
                        yield f"event: token\ndata: {_json.dumps({'text': delta})}\n\n"
            else:
                # Non-streaming fallback (Gemini / Ollama / local)
                if settings.llm_provider == "ollama" and settings.ollama_base_url:
                    try:
                        full_text = await call_ollama(prompt, "You are a clear and simple writer.")
                    except Exception:
                        pass
                if not full_text and settings.google_api_key:
                    try:
                        full_text = await call_gemini(prompt, "You are a clear and simple writer.")
                    except Exception:
                        pass
                if not full_text:
                    full_text = generate_local_fusion_fallback(query_text, matching_articles)
                # Send the full text in one event for non-streaming providers
                yield f"event: token\ndata: {_json.dumps({'text': full_text})}\n\n"

        except Exception as exc:
            logger.error("[Chat Stream] LLM call failed: %s", exc)
            full_text = generate_local_fusion_fallback(query_text, matching_articles)
            yield f"event: token\ndata: {_json.dumps({'text': full_text})}\n\n"

        # Final event with complete response
        response_data = {"summary": full_text, "relevant_articles": relevant_list}
        yield f"event: done\ndata: {_json.dumps(response_data)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


class CompleteRequest(BaseModel):
    system: str
    user: str


@router.post("/complete")
async def chat_complete(payload: CompleteRequest):
    prompt = f"System: {payload.system}\nUser: {payload.user}\nOutput JSON format only."
    reply = ""
    if settings.llm_provider == "ollama" and settings.ollama_base_url:
        try:
            reply = await call_ollama(prompt, payload.system)
        except Exception:
            pass
    if not reply:
        if settings.openai_api_key:
            try:
                reply = await call_openai(prompt, payload.system)
            except Exception:
                pass
        elif settings.google_api_key:
            try:
                reply = await call_gemini(prompt, payload.system)
            except Exception:
                pass
    
    import json
    try:
        match = re.search(r"\{.*\}", reply, re.DOTALL)
        if match:
            obj = json.loads(match.group(0))
            if "summary" in obj and "impact" in obj:
                return obj
    except Exception:
        pass
        
    # Heuristic fallback - plain language only
    title = ""
    content = ""
    for line in payload.user.split("\n"):
        if line.startswith("Title:"):
            title = line[6:].strip()
        elif line.startswith("Content:"):
            content = line[8:].strip()
            
    summary = content[:120] + "..." if len(content) > 120 else content
    if not summary:
        summary = title
    impact = f"Update on: {title[:60]}."
    
    return {
        "summary": summary,
        "impact": impact,
        "confidence": "Medium"
    }


from collections import defaultdict
import time

_rss_cache = {}  # key -> (expiry_time, content_bytes)
_rss_rate_limits = defaultdict(list)  # ip -> list of timestamps


@router.get("/rss")
async def proxy_rss(request: Request, q: str):
    from fastapi import Response
    import httpx
    
    # 1. Rate Limiting (10 requests per minute per IP)
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    # clean old timestamps
    _rss_rate_limits[client_ip] = [t for t in _rss_rate_limits[client_ip] if now - t < 60.0]
    if len(_rss_rate_limits[client_ip]) >= 10:
        raise HTTPException(status_code=429, detail="Too many requests. Please wait before querying RSS again.")
    _rss_rate_limits[client_ip].append(now)
    
    # 2. Cache check (Cache TTL = 2 minutes)
    cache_key = q.strip().lower()
    if cache_key in _rss_cache:
        expiry, cached_content = _rss_cache[cache_key]
        if now < expiry:
            return Response(content=cached_content, media_type="application/xml")
            
    # 3. Fetch from Google News
    url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, timeout=10.0)
            if resp.status_code == 200:
                _rss_cache[cache_key] = (now + 120.0, resp.content)
                return Response(content=resp.content, media_type="application/xml")
            raise HTTPException(status_code=resp.status_code, detail="Google News RSS unavailable")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
