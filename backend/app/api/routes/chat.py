import os
import re
import logging
import math
import asyncio
from collections import Counter
from pathlib import Path
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from backend.app.database import get_db, Article
from backend.app.services.summarizer import call_openai, call_gemini, call_ollama
from backend.app.config import settings
from backend.app.services.job_store import job_store

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
    if not common_terms:
        return 0.0

    dot_product = sum(doc_vector[token] * art_vector[token] for token in common_terms)
    norm_a = math.sqrt(sum(value**2 for value in doc_vector.values()))
    norm_b = math.sqrt(sum(value**2 for value in art_vector.values()))
    base_score = dot_product / (norm_a * norm_b) if norm_a and norm_b else 0.0

    # Boost high-impact or high-confidence reports slightly for precision
    boost = 0.1 if getattr(article, 'impact_level', '') == 'High Impact' else 0.0
    return base_score + boost


async def search_relevant_articles(db: AsyncSession, doc_text: str, limit: int = 5) -> List[Article]:
    from backend.app.services.classifier import get_transformer
    import numpy as np
    
    try:
        transformer = get_transformer()
        query_vector = transformer.encode(doc_text, convert_to_numpy=True)
    except Exception as e:
        logger.error(f"[Chat] Failed to encode query text: {e}")
        stmt = select(Article).order_by(Article.published_at.desc()).limit(limit)
        res = await db.execute(stmt)
        return list(res.scalars().all())

    if db.bind.dialect.name == "postgresql" and HAS_PGVECTOR:
        try:
            stmt = select(Article).order_by(cosine_distance(Article.embedding, query_vector.tolist())).limit(limit)
            result = await db.execute(stmt)
            return list(result.scalars().all())
        except Exception as pg_err:
            logger.warning(f"[Chat] pgvector query failed, falling back to local numpy similarity: {pg_err}")
    
    stmt = select(Article)
    result = await db.execute(stmt)
    articles = result.scalars().all()
    if not articles:
        return []

    scored_articles = []
    query_norm = np.linalg.norm(query_vector)
    if query_norm <= 0:
        return sorted(articles, key=lambda x: x.published_at, reverse=True)[:limit]

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

    if not scored_articles:
        return sorted(articles, key=lambda x: x.published_at, reverse=True)[:limit]

    scored_articles.sort(key=lambda x: x[0], reverse=True)
    return [art for _, art in scored_articles[:limit]]

@router.post("/fusion")
async def upload_and_fuse_document(
    file: UploadFile = File(...),
    instructions: Optional[str] = Form(None)
):
    """
    Accepts a document upload with optional instruction prompts, stores it for background processing,
    and returns a job id immediately. The status endpoint exposes progress.
    """
    temp_dir = Path(__file__).resolve().parents[3] / "scratch"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_file_path = temp_dir / f"uploaded_{uuid_str()}_{file.filename}"

    try:
        content = await file.read()
        temp_file_path.write_bytes(content)
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
        # 2. LLM synthesis
        prompt = f"""
        REAL-TIME NEWS AND STABILITY REPORT
        
        You are an expert communicator who translates complex news into simple, plain English.
        Below is the text extracted from an uploaded document:
        -----------------------------------------------------
        {extracted_text[:3000]}
        -----------------------------------------------------

        Cross-reference this file against the following verified public news reports:
        -----------------------------------------------------
        {article_context}
        -----------------------------------------------------

        OPERATOR INSTRUCTIONS / TASKS TO PERFORM:
        -----------------------------------------------------
        {instructions if instructions else "Provide a general cross-reference news briefing."}
        -----------------------------------------------------

        INSTRUCTIONS:
        1. Deliver a clear, objective, and simple briefing.
        2. Use simple, everyday words. Avoid any jargon, such as "OSINT," "telemetry," "bilaterals," "strategic meetings," "tactical," "reconnaissance," "frontier," etc.
        3. Format using clear headings:
           - **1. OVERVIEW**: Summary of facts, locations, and actions in plain English.
           - **2. NEWS COMPARISON**: Explain the connections between the uploaded document and the public news reports.
           - **3. WHAT THIS MEANS FOR ORDINARY PEOPLE**: Explain the impact on daily citizen safety, costs, travel, or general stability.
        4. You must use a direct, objective, and clear tone. Avoid conversational fillers, jokes, or first-person pronouns.
        5. You MUST cite the news sources where applicable using markdown links (e.g. "[Title of news article](URL)").
        6. If the document has no relation to the news reports, state "NO CORRELATION ESTABLISHED" and write a helpful brief in simple English.
        7. Deliver a detailed and clear response.
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
    md = "NEWS AND STABILITY REPORT (OFFLINE FALLBACK)\n\n"
    
    md += "**1. OVERVIEW**\n"
    md += f"The query or text contains: \n> {doc_text[:350]}...\n\n"
    
    md += "**2. DETAILED ANALYSIS & NEWS COMPARISON**\n"
    if not articles:
        md += "*NO LIVE NEWS FEEDS IN SPECIFIED SECTOR.*\n\n"
    else:
        md += "Matched the following public news reports:\n\n"
        for art in articles:
            md += (
                f"*   **[{art.title}]({art.url or '#'})**\n"
                f"    *   *Source*: {art.source or 'Unknown'}\n"
                f"    *   *Department*: {art.department or 'General'}\n"
                f"    *   *Target*: {art.country_code or 'Global'}\n"
                f"    *   *News*: {art.summary or art.content[:160]}...\n\n"
            )
            
    md += "**3. WHAT THIS MEANS FOR ORDINARY PEOPLE**\n"
    md += "The search matched local reports in the system. Everything is running as usual, and we suggest checking active daily updates for any changes.\n"
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

    # 3. LLM synthesis
    prompt = f"""
    REAL-TIME NEWS AND STABILITY REPORT
    
    You are an expert communicator who translates complex news into simple, plain English.
    
    {history_context}
    
    The user has submitted the following query:
    -----------------------------------------------------
    QUERY: {query_text}
    -----------------------------------------------------

    Analyze and answer this query based on the conversation history and the following verified public news reports:
    -----------------------------------------------------
    {article_context}
    -----------------------------------------------------

    INSTRUCTIONS:
    1. Deliver a clear, objective, and simple response.
    2. Use simple, everyday words. Avoid any jargon, such as "OSINT," "telemetry," "bilaterals," "strategic meetings," "tactical," "reconnaissance," "frontier," etc.
    3. Format using clear headings:
       - **1. OVERVIEW**: Summary of facts, locations, and actions in plain English.
       - **2. DETAILED ANALYSIS**: Clear details explaining who, what, when, and where.
       - **3. WHAT THIS MEANS FOR ORDINARY PEOPLE**: Explain the impact on daily citizen safety, costs, travel, or general stability.
    4. You must use a direct, objective, and clear tone. Avoid conversational fillers, jokes, or first-person pronouns.
    5. You MUST cite the news sources where applicable using markdown links (e.g. "[Title of news article](URL)").
    6. If no relevant information is available in the provided reports, state "NO LIVE NEWS FEEDS IN SPECIFIED SECTOR" and provide a brief general safety assessment in simple terms.
    7. Deliver a detailed and clear response.
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

    return {"summary": fused_summary, "relevant_articles": relevant_list}
