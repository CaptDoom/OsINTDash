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
    """
    Ranking pipeline:
    - Local hybrid retrieval based on term overlap and document similarity
    - Prefer high-impact verified news articles with richer report content
    - Fall back to all available articles if no high-impact matches exist
    """
    stmt = select(Article).where(Article.impact_level == "High Impact")
    result = await db.execute(stmt)
    articles = result.scalars().all()

    if not articles:
        stmt = select(Article)
        result = await db.execute(stmt)
        articles = result.scalars().all()

    if not articles:
        return []

    scored_articles = []
    for art in articles:
        score = score_article_similarity(doc_text, art)
        if score > 0:
            scored_articles.append((score, art))

    if not scored_articles:
        return []

    scored_articles.sort(key=lambda x: x[0], reverse=True)
    return [art for _, art in scored_articles[:limit]]

@router.post("/fusion")
async def upload_and_fuse_document(
    file: UploadFile = File(...)
):
    """
    Accepts a document upload, stores it for background processing, and returns a job id immediately.
    The status endpoint exposes parsing/searching/synthesizing progress for polling.
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
        },
    )

    if settings.enable_inline_job_processing:
        asyncio.create_task(process_fusion_job(job.job_id, str(temp_file_path), file.filename))

    return {"job_id": job.job_id, "status": job.status, "progress": job.progress}


@router.get("/fusion/status/{job_id}")
async def fusion_job_status(job_id: str):
    job = await job_store.get("fusion", job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Fusion job not found")
    return job.__dict__


async def process_fusion_job(job_id: str, temp_file_path: str, filename: str):
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
            if filename.lower().endswith(".pdf"):
                extracted_text = extract_simple_pdf_text(temp_file_path)
            elif filename.lower().endswith(".docx"):
                extracted_text = extract_simple_docx_text(temp_file_path)
            else:
                with open(temp_file_path, "r", encoding="utf-8", errors="ignore") as handle:
                    extracted_text = handle.read()

        if not extracted_text.strip():
            raise HTTPException(status_code=400, detail="The uploaded document contains no readable text.")

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
        prompt = f"""
        You are an expert geopolitical intelligence analyst.
        Below is the text extracted from an internal user document:
        -----------------------------------------------------
        {extracted_text[:3000]}
        -----------------------------------------------------

        Cross-reference this internal file against the following verified public OSINT news reports:
        -----------------------------------------------------
        {article_context}
        -----------------------------------------------------

        Write a cohesive Fused Intelligence Report summarizing the overlap and connections.
        You MUST cite the news sources where applicable (e.g., "[Title of news article](URL)").
        If the document has no relation to the news reports, state that clearly but write a helpful brief.
        """

        fused_summary = ""
        if settings.llm_provider == "ollama" and settings.ollama_base_url:
            try:
                fused_summary = await call_ollama(prompt, "You are a Senior Intel Fusion Officer.")
            except Exception as exc:
                logger.warning("[Chat] Ollama call failed, falling back: %s", exc)
        
        if not fused_summary:
            if settings.openai_api_key:
                fused_summary = await call_openai(prompt, "You are a Senior Intel Fusion Officer.")
            elif settings.google_api_key:
                fused_summary = await call_gemini(prompt, "You are a Senior Intel Fusion Officer.")
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
    md = "# Fused Intelligence Summary (Offline Fallback Mode)\n\n"
    md += "Unable to query cloud LLM APIs. Generating standard semantic overlap details:\n\n"
    
    md += "### User Document Context Snippet\n"
    md += f"> {doc_text[:350]}...\n\n"
    
    md += "### Connected Public Reports\n"
    if not articles:
        md += "*No semantic overlap could be established with current high-impact news archives.*\n"
    for art in articles:
        md += (
            f"* **[{art.title}]({art.url})** (Source: {art.source} | Department: {art.department} | Target: {art.country_code})\n"
            f"  {art.summary or art.content[:160]}...\n\n"
        )
    md += "### Fusion Insight\n"
    md += "The best matched sources above were selected based on shared terminology and verified impact signals. Review the summarized report for focus areas and supporting evidence.\n"
    return md


class ChatQuery(BaseModel):
    query: str


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

    # 2. LLM synthesis
    prompt = f"""
    You are an expert geopolitical intelligence analyst.
    The user is asking a live query regarding recent events:
    -----------------------------------------------------
    Query: {query_text}
    -----------------------------------------------------

    Answer this query based on the following verified public OSINT news reports:
    -----------------------------------------------------
    {article_context}
    -----------------------------------------------------

    Write a precise, concise, and real-time AI news briefing answering the user's query.
    You MUST cite the news sources where applicable (e.g., "[Title of news article](URL)").
    If no relevant information is in the reports, answer to the best of your ability but clarify what the verified sources say.
    """

    fused_summary = ""
    if settings.llm_provider == "ollama" and settings.ollama_base_url:
        try:
            fused_summary = await call_ollama(prompt, "You are a Senior Intel Fusion Officer.")
        except Exception as exc:
            logger.warning("[Chat] Ollama call failed, falling back: %s", exc)
            
    if not fused_summary:
        if settings.openai_api_key:
            fused_summary = await call_openai(prompt, "You are a Senior Intel Fusion Officer.")
        elif settings.google_api_key:
            fused_summary = await call_gemini(prompt, "You are a Senior Intel Fusion Officer.")
        else:
            fused_summary = generate_local_fusion_fallback(query_text, matching_articles)

    return {"summary": fused_summary, "relevant_articles": relevant_list}
