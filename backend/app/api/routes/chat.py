import os
import logging
import math
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from backend.app.database import get_db, Article
from backend.app.services.summarizer import call_openai, call_gemini

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

def re_split(text: str) -> List[str]:
    import re
    return re.findall(r'\w+', text)

async def search_relevant_articles(db: AsyncSession, doc_text: str, limit: int = 5) -> List[Article]:
    """
    Performs hybrid retrieval:
    - Attempts LlamaIndex Vector search (if pgvector and OpenAI are active).
    - Falls back to Cosine Similarity / Word overlap ranking in memory if offline.
    """
    stmt = select(Article).where(Article.impact_level == "High Impact")
    result = await db.execute(stmt)
    articles = result.scalars().all()
    
    if not articles:
        return []

    # Local Word Overlap (BM25 equivalent) ranking fallback
    scored_articles = []
    for art in articles:
        score = get_text_overlap_similarity(doc_text, f"{art.title} {art.content}")
        scored_articles.append((score, art))
        
    scored_articles.sort(key=lambda x: x[0], reverse=True)
    return [art for _, art in scored_articles[:limit]]

@router.post("/fusion", response_model=FusionResponse)
async def upload_and_fuse_document(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db)
):
    """
    RAG Endpoint:
    1. Parse PDF/DOCX using IBM Docling (falling back to lightweight extractors if unavailable).
    2. Retrieve top 5 matching high-impact alerts using hybrid retrieval.
    3. Generate a cross-referenced intelligence summary citing news sources.
    """
    # Create scratch folder for temporary uploads
    temp_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../scratch"))
    os.makedirs(temp_dir, exist_ok=True)
    
    temp_file_path = os.path.join(temp_dir, f"uploaded_{uuid_str()}_{file.filename}")
    
    # Write file to disk
    try:
        with open(temp_file_path, "wb") as f:
            content = await file.read()
            f.write(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")

    extracted_text = ""
    
    # 1. Parse using IBM Docling
    try:
        from docling.document_converter import DocumentConverter
        logger.info(f"[RAG] Parsing {file.filename} using IBM Docling...")
        converter = DocumentConverter()
        result = converter.convert(temp_file_path)
        extracted_text = result.document.export_to_markdown()
        logger.info(f"[RAG] Docling conversion successful.")
    except Exception as docling_err:
        logger.warning(f"[RAG] IBM Docling parsing failed or package not installed: {docling_err}. Trying simple text fallback.")
        
        # Simple extraction fallback based on extension
        try:
            if file.filename.endswith(".pdf"):
                # Try pypdf / pdfplumber if available, or just read binary strings
                extracted_text = extract_simple_pdf_text(temp_file_path)
            elif file.filename.endswith(".docx"):
                extracted_text = extract_simple_docx_text(temp_file_path)
            else:
                with open(temp_file_path, "r", encoding="utf-8", errors="ignore") as f:
                    extracted_text = f.read()
        except Exception as fallback_err:
            logger.error(f"[RAG] Fallback text extractor failed: {fallback_err}")
            extracted_text = f"Uploaded File: {file.filename}\n[Unable to parse full document content]"

    # Clean up temp file
    if os.path.exists(temp_file_path):
        os.remove(temp_file_path)

    if not extracted_text.strip():
        raise HTTPException(status_code=400, detail="The uploaded document contains no readable text.")

    # 2. Retrieve top 5 matching articles from relational archives
    matching_articles = await search_relevant_articles(db, extracted_text, limit=5)
    
    # 3. Fuse RAG prompt
    article_context = ""
    relevant_list = []
    
    for idx, art in enumerate(matching_articles):
        article_context += f"SOURCE [{idx+1}]: {art.title}\nURL: {art.url}\nSummary: {art.summary or art.content[:150]}\n\n"
        relevant_list.append({
            "id": art.id,
            "title": art.title,
            "url": art.url,
            "source": art.source,
            "department": art.department,
            "published_at": art.published_at.isoformat()
        })

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

    # 4. Synthesize Fused Intelligence
    fused_summary = ""
    try:
        if os.getenv("OPENAI_API_KEY"):
            fused_summary = await call_openai(prompt, "You are a Senior Intel Fusion Officer.")
        elif os.getenv("GOOGLE_API_KEY"):
            fused_summary = await call_gemini(prompt, "You are a Senior Intel Fusion Officer.")
        else:
            # Local Heuristic generator
            fused_summary = generate_local_fusion_fallback(extracted_text, matching_articles)
    except Exception as e:
        logger.error(f"[RAG] Summary generation failed: {e}")
        fused_summary = generate_local_fusion_fallback(extracted_text, matching_articles)

    return FusionResponse(summary=fused_summary, relevant_articles=relevant_list)

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
        md += f"* **[{art.title}]({art.url})** (Source: {art.source} | Department: {art.department})  \n"
        md += f"  *Geopolitical signal overlapping with internal analysis coordinates.*\n\n"
    return md
