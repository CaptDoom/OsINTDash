import logging
import asyncio
from datetime import datetime, timedelta
from typing import List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.config import settings
from backend.app.database import Article, ArchiveSummary

logger = logging.getLogger("drishya.summarizer")

# Async LLM wrappers with safe fallback
async def call_gemini(prompt: str, system_instruction: str = "You are an intelligence analyst.") -> str:
    if not settings.GOOGLE_API_KEY:
        raise ValueError("GOOGLE_API_KEY is not set.")
    try:
        # Import dynamically to avoid crash if not installed
        from google import genai
        client = genai.Client(api_key=settings.GOOGLE_API_KEY)
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=dict(
                system_instruction=system_instruction,
                temperature=0.2
            )
        )
        return response.text.strip()
    except Exception as e:
        logger.error(f"[Summarizer] Gemini call failed: {e}")
        raise e

async def call_openai(prompt: str, system_instruction: str = "You are a senior analyst.") -> str:
    if not settings.OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is not set.")
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        response = await client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"[Summarizer] OpenAI call failed: {e}")
        raise e

def generate_local_heuristic_summary(articles: List[Article], timeframe: str) -> str:
    """Heuristic summary builder in case LLM APIs are not configured."""
    logger.info("[Summarizer] Using local heuristic summarizer fallback.")
    by_dept = {
        "Military & Defense": [],
        "Economic & Financial": [],
        "Social Affairs & Welfare": [],
        "Political & Diplomatic": []
    }
    
    for art in articles:
        dept = art.department if art.department in by_dept else "Political & Diplomatic"
        by_dept[dept].append(art)
        
    markdown = f"# Executive OSINT Briefing ({timeframe})\n"
    markdown += f"Generated at: {datetime.utcnow().isoformat()} (Heuristic Summary Fallback Mode)\n\n"
    markdown += "This briefing consolidates military activity, border posture changes, and geopolitical risk metrics.\n\n"
    
    for dept, arts in by_dept.items():
        markdown += f"## {dept}\n"
        if not arts:
            markdown += "*No high impact events recorded in this sector.*\n\n"
            continue
            
        markdown += f"*Total verified alerts: {len(arts)}*\n\n"
        for i, art in enumerate(arts[:4]):
            summary_text = art.summary if art.summary else art.content[:160] + "..."
            markdown += f"**{i+1}. [{art.title}]({art.url})**  \n"
            markdown += f"Source: {art.source} | Target: {art.country_code}  \n"
            markdown += f"{summary_text}  \n\n"
            
    return markdown

async def generate_archive_summary(timeframe: str, db: AsyncSession) -> str:
    """
    Executes a Map-Reduce summary across archived high-impact articles.
    Saves outputs in the database for caching.
    """
    # 1. Check Cache
    stmt = select(ArchiveSummary).where(ArchiveSummary.timeframe == timeframe)
    cached = (await db.execute(stmt)).scalar_one_or_none()
    if cached:
        # Cache hits for summaries return immediately (valid for 3 hours)
        age = datetime.utcnow() - cached.generated_at
        if age < timedelta(hours=3):
            logger.info(f"[Summarizer] Summary cache hit for timeframe: {timeframe}")
            return cached.summary
        else:
            await db.delete(cached)
            await db.commit()

    # 2. Get articles in timeframe
    now = datetime.utcnow()
    if timeframe == "1M":
        start_date = now - timedelta(days=30)
    elif timeframe == "6M":
        start_date = now - timedelta(days=180)
    elif timeframe == "1Y":
        start_date = now - timedelta(days=365)
    else:
        start_date = now - timedelta(days=30) # Default 1M

    stmt = select(Article).where(
        Article.impact_level == "High Impact",
        Article.published_at >= start_date
    ).order_by(Article.published_at.desc())
    
    articles = (await db.execute(stmt)).scalars().all()
    
    if not articles:
        return f"# Executive OSINT Briefing ({timeframe})\n\nNo high impact events detected within this archive window."

    # 3. LLM check: fallback if keys are missing
    if not settings.GOOGLE_API_KEY or not settings.OPENAI_API_KEY:
        summary_md = generate_local_heuristic_summary(articles, timeframe)
        # Store in cache table
        new_summary = ArchiveSummary(timeframe=timeframe, summary=summary_md)
        db.add(new_summary)
        await db.commit()
        return summary_md

    try:
        logger.info(f"[Summarizer] Commencing Map-Reduce summary for {len(articles)} articles.")
        
        # MAP Step: Summarize in batches of 5
        batch_summaries = []
        batches = [articles[i:i + 5] for i in range(0, len(articles), 5)]
        
        async def map_batch(batch, index):
            text_block = ""
            for a in batch:
                text_block += f"Title: {a.title}\nDept: {a.department}\nContent: {a.content[:300]}\n---\n"
            
            prompt = f"Analyze these 5 geopolitical alerts and write a structured 4-sentence summary highlighting threat levels and major actors:\n\n{text_block}"
            sys_inst = "You are a military intelligence analyst. Extract threat indicators."
            
            # Use Gemini for parallel Map processing
            return await call_gemini(prompt, sys_inst)
            
        tasks = [map_batch(b, i) for i, b in enumerate(batches[:15])] # Limit to top 75 articles to avoid API exhaustion
        map_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        valid_map_results = [r for r in map_results if isinstance(r, str)]
        
        # REDUCE Step: Combine and write final report
        combined_summaries = "\n\n".join(valid_map_results)
        
        reduce_prompt = f"""
        Compile these local intelligence summaries into a single, cohesive Executive Briefing report for the timeframe: {timeframe}.
        The briefing MUST be structured as standard markdown with sections grouped by the following Departments:
        - Military & Defense
        - Economic & Financial
        - Social Affairs & Welfare
        - Political & Diplomatic

        Incorporate any critical action alerts, and write a summary for each department. Ensure it reads like a finished, professional intelligence dossier.

        Source summaries:
        {combined_summaries}
        """
        
        final_summary = await call_openai(reduce_prompt, "You are a Senior Strategic Intel Officer. Compile the final executive report.")
        
        # Cache the result
        new_summary = ArchiveSummary(timeframe=timeframe, summary=final_summary)
        db.add(new_summary)
        await db.commit()
        
        return final_summary
    except Exception as e:
        logger.error(f"[Summarizer] Map-Reduce process failed: {e}. Falling back to heuristics.")
        # Fallback to local heuristic aggregation
        summary_md = generate_local_heuristic_summary(articles, timeframe)
        return summary_md
