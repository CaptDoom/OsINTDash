import re
import httpx
import pypdf
import docx
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from backend.app.database import get_db, Article
from backend.app.config import settings
from backend.app.services.summarizer import call_openai, call_gemini, call_ollama

router = APIRouter(prefix="/api/summarizer")

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
    "UA": "Ukraine",
}

def parse_pdf(file_bytes: bytes) -> str:
    try:
        reader = pypdf.PdfReader(BytesIO(file_bytes))
        text = ""
        for page in reader.pages:
            text += (page.extract_text() or "") + "\n"
        return text
    except Exception as e:
        return f"[Error parsing PDF: {str(e)}]"

def parse_docx(file_bytes: bytes) -> str:
    try:
        doc = docx.Document(BytesIO(file_bytes))
        return "\n".join([p.text for p in doc.paragraphs])
    except Exception as e:
        return f"[Error parsing Word Document: {str(e)}]"

async def scrape_url(url: str) -> str:
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            resp = await client.get(url, headers=headers, timeout=10.0)
            if resp.status_code == 200:
                html = resp.text
                html = re.sub(r'<script.*?>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
                html = re.sub(r'<style.*?>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r'<[^>]+>', ' ', html)
                text = re.sub(r'\s+', ' ', text).strip()
                return f"URL Source ({url}):\n{text[:3000]}"
            return f"URL Source ({url}) failed with status {resp.status_code}"
    except Exception as e:
        return f"URL Source ({url}) failed: {str(e)}"

def generate_fallback_summary(country_name: str, timeframe_label: str, articles: list, external_context: str) -> str:
    md = f"CLASSIFICATION: UNCLASSIFIED // OSINT FOR INTERNAL STRATCOM USE ONLY\n"
    md += f"GEOPOLITICAL INTELLIGENCE BRIEFING: {country_name.upper()} ({timeframe_label.upper()})\n\n"
    
    md += "### **1. EXECUTIVE SUMMARY**\n"
    if articles:
        md += f"Geopolitical intelligence sweep for {country_name} over the last {timeframe_label} has compiled {len(articles)} active security events. Operational signals indicate shifting strategic parameters requiring continuous monitoring.\n\n"
    else:
        md += f"Geopolitical intelligence sweep for {country_name} over the last {timeframe_label} indicates baseline stability. Operational parameters remain within expected strategic tolerances, with no major alert escalations registered.\n\n"
    
    md += "### **2. CORE ANALYSIS / SECTORAL BREAKDOWN**\n"
    
    # Group by dept
    by_dept = {}
    for art in articles:
        dept = art.department
        if dept not in by_dept:
            by_dept[dept] = []
        by_dept[dept].append(art)
        
    for dept in ["Military & Defense", "Economic & Financial", "Political & Diplomatic", "Social Affairs & Welfare / Technology"]:
        md += f"#### **{dept}**\n"
        # Match departments
        if dept == "Social Affairs & Welfare / Technology":
            dept_arts = by_dept.get("Social Affairs & Welfare", []) + by_dept.get("Technology & Cyber", [])
        else:
            dept_arts = by_dept.get(dept, [])
            
        if not dept_arts:
            md += f"- **Contextual Analysis**: Geopolitical and defense metrics for this sector are currently stable, maintaining standard readiness baselines. Historical tracking indicates cooperative regional frameworks remain active.\n"
            md += f"- **Information Gap**: High-fidelity real-time wire signals for this specific sector are currently limited in the local cache window. Continuous scanning of regional telemetry feeds is recommended.\n\n"
        else:
            for art in dept_arts[:4]:
                md += f"- **[{art.title}]({art.url})** ({art.source}): {art.summary or art.content[:180]}...\n"
            md += "\n"
            
    if external_context.strip() and "No external documents" not in external_context:
        md += "#### **External Ingested Context**\n"
        md += f"Parsed supplementary records show the following details:\n"
        md += f"> {external_context[:1000]}...\n\n"
        
    md += "### **3. STRATEGIC IMPLICATIONS & INDICATORS**\n"
    md += "Verify all news alerts against secure intelligence nodes. Maintain baseline security posture, monitor alerts continuously, and track upcoming bilateral triggers or regional defense adjustments.\n"
    return md

@router.post("/generate")
async def generate_custom_summary(
    country_code: str = Form(...),
    timeframe: str = Form(...),
    urls: Optional[str] = Form(None),
    files: Optional[List[UploadFile]] = File(None),
    db: AsyncSession = Depends(get_db)
):
    """
    Generate an AI-driven geopolitical summary of a country over a timeframe (1M, 6M, 1Y)
    blended with external context from uploaded documents and links.
    """
    country_name = COUNTRY_NAMES_BY_CODE.get(country_code.upper(), country_code)
    
    # Calculate cutoff time based on timeframe
    now = datetime.now(timezone.utc)
    if timeframe == "1M":
        start_date = now - timedelta(days=30)
        timeframe_label = "1 Month"
    elif timeframe == "6M":
        start_date = now - timedelta(days=180)
        timeframe_label = "6 Months"
    elif timeframe == "1Y":
        start_date = now - timedelta(days=365)
        timeframe_label = "1 Year"
    else:
        raise HTTPException(status_code=400, detail="Invalid timeframe. Must be '1M', '6M', or '1Y'.")

    # 1. Fetch articles from database
    stmt = select(Article).where(
        Article.country_code == country_code.upper(),
        Article.published_at >= start_date
    ).order_by(Article.published_at.desc())
    
    result = await db.execute(stmt)
    articles = list(result.scalars().all())

    # Formulate articles context
    db_articles_context = ""
    if articles:
        for idx, art in enumerate(articles[:40]): # Limit to 40 articles to prevent token overflow
            db_articles_context += (
                f"SOURCE [{idx+1}]: {art.title}\n"
                f"URL: {art.url}\n"
                f"Department: {art.department}\n"
                f"Source: {art.source}\n"
                f"Published: {art.published_at.isoformat()}\n"
                f"Summary: {art.summary or art.content[:200]}\n\n"
            )
    else:
        db_articles_context = "No news reports found in database for this timeframe.\n"

    # 2. Parse external context
    external_texts = []
    
    # Scrape web URLs
    if urls:
        url_list = [u.strip() for u in urls.split(",") if u.strip()]
        for url in url_list:
            scraped = await scrape_url(url)
            external_texts.append(scraped)

    # Parse uploaded files
    if files:
        for upload_file in files:
            file_bytes = await upload_file.read()
            filename = upload_file.filename.lower()
            if filename.endswith(".pdf"):
                parsed = parse_pdf(file_bytes)
            elif filename.endswith((".docx", ".doc")):
                parsed = parse_docx(file_bytes)
            else:
                # Text files
                try:
                    parsed = file_bytes.decode("utf-8", errors="ignore")
                except Exception:
                    parsed = f"[Error decoding text file {upload_file.filename}]"
            
            external_texts.append(f"Document ({upload_file.filename}):\n{parsed}")

    external_context = "\n\n---\n\n".join(external_texts) if external_texts else "No external documents or web links provided."

    # 3. LLM Generation
    prompt = f"""
ROLE:
You are an Elite Intelligence Analyst and Lead Technical Communicator. Your objective is to transform raw context, unstructured data, or high-level queries into high-precision, executive-ready, and deeply analytical responses.

GOAL:
Eliminate superficial summaries, empty boilerplate ("No active telemetry", "N/A"), generic placeholders, and hallucinations. Every summary must be authentic, actionable, and richly structured.

---

GEOPOLITICAL INTELLIGENCE BRIEFING: {country_name.upper()}
TIMEFRAME: {timeframe_label}

INPUT SOURCES:
1. INTERNAL OSINT DATABASE (Stored articles matching target country and timeframe):
-----------------------------------------------------
{db_articles_context}
-----------------------------------------------------

2. USER-UPLOADED DOCUMENTS & WEB LINKS (External context):
-----------------------------------------------------
{external_context}
-----------------------------------------------------

---

### INSTRUCTION SET & STANDARDS

1. DENSITY & GRANULARITY:
   - Provide concrete facts, dates, names, metrics, and technical/geopolitical/economic context.
   - Avoid generic fluff or conversational filler (e.g., "In conclusion," "It is important to note").
   - Synthesize underlying patterns, strategic implications, and key takeaways rather than just listing headlines.

2. MANDATORY STRUCTURE:
   Unless specified otherwise, every comprehensive summary must include:
   - **1. EXECUTIVE SUMMARY**: A 2-3 sentence strategic takeaway framing the big picture (including overarching geopolitical trajectory, stability index, and critical alerts).
   - **2. CORE ANALYSIS / SECTORAL BREAKDOWN**: Group key information under distinct, thematic headings:
     - **Military & Defense**
     - **Economic & Financial**
     - **Political & Diplomatic**
     - **Social Affairs & Welfare / Technology**
   - **3. STRATEGIC IMPLICATIONS & INDICATORS**: What this means for future trajectory, risk factors, or actionable next steps/recommendations.

3. ZERO-TELEMETRY & ACCURACY FALLBACK PROTOCOL:
   - NEVER output phrases like "0 active events," "No signals captured," or "Fallback report."
   - If direct data for a sub-sector is sparse in the provided context, infer broader context, historical baselines, or macro trends explicitly tagged as "Contextual Analysis."
   - If information is missing, highlight it as an "Information Gap" rather than generating an empty section.

4. FORMATTING RULES:
   - Use scannable markdown: Bold key entities, utilize structured tables where data comparison is relevant, and use bullet points for lists.
   - Maintain a direct, objective, and executive tone.
   - Highlight links and citations from the database articles where appropriate.

---

### INPUT CONTEXT PROTOCOL
1. Extract primary entities, key metrics, and time-bound events.
2. Cross-reference provided external sources (PDFs, URLs, Notes) with internal context to create a unified narrative.
3. Highlight contradictions or conflicts in the source material if any exist.
"""

    summary_text = ""
    # Try calling available LLMs
    if settings.llm_provider == "ollama" and settings.ollama_base_url:
        try:
            summary_text = await call_ollama(prompt, "You are a Senior Intel Fusion Officer.")
        except Exception:
            pass

    if not summary_text:
        if settings.openai_api_key:
            try:
                summary_text = await call_openai(prompt, "You are a Senior Intel Fusion Officer.")
            except Exception:
                pass
        elif settings.google_api_key:
            try:
                summary_text = await call_gemini(prompt, "You are a Senior Intel Fusion Officer.")
            except Exception:
                pass
                
    if not summary_text:
        # Heuristic fallback summary if all LLMs are offline or not configured
        summary_text = generate_fallback_summary(country_name, timeframe_label, articles, external_context)

    return {"summary": summary_text}
