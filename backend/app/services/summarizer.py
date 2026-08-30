from __future__ import annotations

import asyncio
import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import settings
from backend.app.database import Article, ArchiveSummary

logger = logging.getLogger("drishya.summarizer")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def _redis_client():
    try:
        from backend.app.redis_pool import get_redis_pool
        return await get_redis_pool()
    except Exception:
        return None


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _article_block(article: Article) -> str:
    summary = article.summary or article.content[:220]
    return (
        f"Title: {article.title}\n"
        f"Department: {article.department}\n"
        f"Country: {article.country_code}\n"
        f"Published: {article.published_at.isoformat()}\n"
        f"Source: {article.source}\n"
        f"Summary: {summary}\n"
    )


def _local_summary(articles: List[Article], timeframe: str) -> str:
    """Generate a structured briefing from articles without an LLM."""
    by_dept: Dict[str, List[Article]] = {}
    for article in articles:
        dept = article.department or "Unclassified"
        if dept not in by_dept:
            by_dept[dept] = []
        by_dept[dept].append(article)

    # Count by country
    country_counts: Dict[str, int] = {}
    for article in articles:
        cc = article.country_code or "Unknown"
        country_counts[cc] = country_counts.get(cc, 0) + 1
    top_countries = sorted(country_counts.items(), key=lambda x: x[1], reverse=True)[:8]

    # Count by impact
    high = sum(1 for a in articles if a.impact_level == "High Impact")
    medium = sum(1 for a in articles if a.impact_level == "Medium Impact")
    normal = sum(1 for a in articles if a.impact_level == "Normal Impact")

    markdown = f"# News Briefing ({timeframe})\n"
    markdown += f"Generated: {_utc_now().isoformat()}\n\n"
    
    # Executive summary
    markdown += f"## Executive Summary\n"
    markdown += f"**{len(articles)} total articles** across {len(by_dept)} sectors. "
    markdown += f"Impact breakdown: **{high}** high, **{medium}** medium, **{normal}** normal.\n\n"
    
    if top_countries:
        markdown += "**Top countries:** " + ", ".join(f"{cc} ({n})" for cc, n in top_countries) + "\n\n"
    
    # Per-department sections
    dept_order = ["Military & Defense", "Economic & Financial", "Political & Diplomatic", "Social Affairs & Welfare", "Technology & Cyber"]
    all_depts = list(by_dept.keys())
    ordered_depts = dept_order + [d for d in all_depts if d not in dept_order]
    
    for dept in ordered_depts:
        grouped = by_dept.get(dept, [])
        if not grouped:
            continue
        markdown += f"## {dept}\n"
        markdown += f"*{len(grouped)} updates*\n\n"
        for index, article in enumerate(grouped[:5], start=1):
            summary_text = article.summary or article.content[:160] + "..." if article.content else "No summary available."
            source_rep = getattr(article, "source_reputation", "") or ""
            rep_badge = f" [{source_rep}]" if source_rep and source_rep != "Unrated" else ""
            markdown += f"**{index}. [{article.title}]({article.url})**  \n"
            markdown += f"Source: {article.source or 'Unknown'}{rep_badge} | {article.country_code}  \n"
            markdown += f"{summary_text}  \n\n"
    
    return markdown


async def call_gemini(prompt: str, system_instruction: str = "You are an intelligence analyst.") -> str:
    if not settings.google_api_key:
        raise ValueError("GOOGLE_API_KEY is not set.")
    try:
        from google import genai

        client = genai.Client(api_key=settings.google_api_key)
        response = client.models.generate_content(
            model=os.getenv("GOOGLE_MODEL", "gemini-2.5-flash"),
            contents=prompt,
            config=dict(system_instruction=system_instruction, temperature=0.2),
        )
        return response.text.strip()
    except Exception as exc:
        logger.error("[Summarizer] Gemini call failed: %s", exc)
        raise


async def call_openai(prompt: str, system_instruction: str = "You are a senior analyst.") -> str:
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY is not set.")
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openai_api_key)
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        logger.error("[Summarizer] OpenAI call failed: %s", exc)
        raise


async def call_ollama(prompt: str, system_instruction: str = "You are a senior analyst.") -> str:
    if not settings.ollama_base_url:
        raise ValueError("OLLAMA_BASE_URL is not set.")
    import httpx
    model = settings.llm_model or "llama3.1:8b-instruct"
    url = f"{settings.ollama_base_url.rstrip('/')}/api/chat"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt},
        ],
        "stream": False
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, timeout=30.0)
        if response.status_code == 200:
            data = response.json()
            return data.get("message", {}).get("content", "").strip()
        else:
            raise ValueError(f"Ollama status {response.status_code}: {response.text}")


async def _compose_single_pass_summary(articles: List[Article], timeframe: str) -> str:
    article_context = "\n---\n".join(_article_block(article) for article in articles)
    prompt = f"""
You are an expert communicator who translates complex news into simple, plain English.
Write a cohesive news and stability briefing for the timeframe {timeframe}.
Use simple, everyday words. Avoid any jargon, such as "OSINT," "telemetry," "bilaterals," "strategic meetings," "tactical," "reconnaissance," "frontier," etc.
Use the articles below as the entire source set.
Return markdown with sections for Military & Defense, Economic & Financial, Social Affairs & Welfare, and Political & Diplomatic.
Add a final section called "WHAT THIS MEANS FOR ORDINARY PEOPLE" explaining the impact in plain English.
Keep it coherent and avoid repetitive phrasing.

Source articles:
{article_context}
"""
    if settings.llm_provider == "ollama" and settings.ollama_base_url:
        try:
            return await call_ollama(prompt, "You are a clear and simple writer.")
        except Exception as exc:
            logger.warning("[Summarizer] Ollama call failed, falling back: %s", exc)
    if settings.openai_api_key:
        return await call_openai(prompt, "You are a clear and simple writer.")
    if settings.google_api_key:
        return await call_gemini(prompt, "You are a clear and simple writer.")
    return _local_summary(articles, timeframe)


async def _compose_recursive_summary(articles: List[Article], timeframe: str) -> str:
    batch_size = settings.archive_summary_chunk_size
    batches = [articles[index : index + batch_size] for index in range(0, len(articles), batch_size)]

    async def summarize_batch(batch: List[Article]) -> str:
        return await _compose_single_pass_summary(batch, timeframe)

    map_results = await asyncio.gather(*(summarize_batch(batch) for batch in batches[:20]), return_exceptions=True)
    summaries = [result for result in map_results if isinstance(result, str) and result.strip()]
    if not summaries:
        return _local_summary(articles, timeframe)

    reduce_prompt = f"""
Combine these partial news summaries into a single clear news and stability briefing for {timeframe}.
Use simple, everyday words. Avoid any jargon, such as "OSINT," "telemetry," "bilaterals," "strategic meetings," "tactical," "reconnaissance," "frontier," etc.
Preserve the main findings and remove repetition.

Partial summaries:
{chr(10).join(f'- {summary}' for summary in summaries)}
"""
    if settings.llm_provider == "ollama" and settings.ollama_base_url:
        try:
            return await call_ollama(reduce_prompt, "You are a clear and simple writer.")
        except Exception as exc:
            logger.warning("[Summarizer] Ollama call failed, falling back: %s", exc)
    if settings.openai_api_key:
        return await call_openai(reduce_prompt, "You are a clear and simple writer.")
    if settings.google_api_key:
        return await call_gemini(reduce_prompt, "You are a clear and simple writer.")
    return _local_summary(articles, timeframe)


async def _read_cache(timeframe: str, version: str) -> Optional[str]:
    from backend.app.redis_pool import cache_get
    cache_key = f"drishya:archive-summary:{timeframe}:{version}"
    cached = await cache_get(cache_key)
    if cached and isinstance(cached, str):
        return cached
    return None


async def _write_cache(timeframe: str, version: str, summary: str) -> None:
    from backend.app.redis_pool import cache_set
    cache_key = f"drishya:archive-summary:{timeframe}:{version}"
    await cache_set(cache_key, summary, ttl=settings.archive_cache_ttl_seconds)


async def _get_archive_version() -> str:
    from backend.app.redis_pool import cache_get
    pool = await _redis_client()
    if not pool:
        return "0"
    try:
        version = await pool.get("drishya:archive:version")
        return version or "0"
    except Exception:
        return "0"


async def generate_archive_summary(timeframe: str, db: AsyncSession) -> str:
    from backend.app.observability import metrics

    metrics.state.archive_summary_requests_total += 1
    version = await _get_archive_version()
    cached = await _read_cache(timeframe, version)
    if cached:
        logger.info("[Summarizer] Cache hit for timeframe %s version %s", timeframe, version)
        return cached

    now = _utc_now()
    if timeframe == "1M":
        start_date = now - timedelta(days=30)
    elif timeframe == "6M":
        start_date = now - timedelta(days=180)
    elif timeframe == "1Y":
        start_date = now - timedelta(days=365)
    else:
        start_date = now - timedelta(days=30)

    stmt = select(Article).where(Article.impact_level == "High Impact", Article.published_at >= start_date).order_by(Article.published_at.desc())
    articles = (await db.execute(stmt)).scalars().all()
    if not articles:
        summary = f"# News Briefing ({timeframe})\n\nNo major events found in this time period. Things are quiet and stable."
        await _write_cache(timeframe, version, summary)
        return summary

    token_estimate = _estimate_tokens("\n".join(_article_block(article) for article in articles))
    if token_estimate <= settings.archive_max_tokens:
        summary = await _compose_single_pass_summary(articles, timeframe)
    else:
        summary = await _compose_recursive_summary(articles, timeframe)

    await _write_cache(timeframe, version, summary)

    cached_row = ArchiveSummary(timeframe=timeframe, summary=summary)
    db.add(cached_row)
    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.debug("[Summarizer] Cache row write skipped: %s", exc)

    return summary


async def generate_archive_field_summary(timeframe: str, department: Optional[str], db: AsyncSession) -> str:
    now = _utc_now()
    if timeframe == "1M":
        start_date = now - timedelta(days=30)
    elif timeframe == "6M":
        start_date = now - timedelta(days=180)
    elif timeframe == "1Y":
        start_date = now - timedelta(days=365)
    else:
        start_date = now - timedelta(days=30)

    # First, query high impact articles matching timeframe and optionally department
    stmt = select(Article).where(Article.impact_level == "High Impact", Article.published_at >= start_date)
    if department:
        stmt = stmt.where(Article.department == department)
    stmt = stmt.order_by(Article.published_at.desc())
    
    articles = (await db.execute(stmt)).scalars().all()
    if not articles:
        # Fall back to include normal/medium impact if no high impact articles exist
        stmt = select(Article).where(Article.published_at >= start_date)
        if department:
            stmt = stmt.where(Article.department == department)
        stmt = stmt.order_by(Article.published_at.desc())
        articles = (await db.execute(stmt)).scalars().all()

    if not articles:
        return f"# News Update: {department or 'All Fields'} ({timeframe})\n\nNo reports found for this area in this time period."

    # Take up to 35 most relevant/recent articles to summarize to avoid token limits
    articles = list(articles)[:35]
    
    article_context = "\n---\n".join(_article_block(article) for article in articles)
    
    field_name = department or "All Fields"
    prompt = f"""
    Summarize the news below about {field_name} over {timeframe}.
    Write in plain, everyday English. No military or intelligence jargon.
    Be direct and concise. Every sentence must add new information.
    
    NEWS ARTICLES:
    {article_context}
    
    FORMAT:
    **Key Developments** - Bulleted list of what happened (2-5 bullets).
    **Impact** - What this means for everyday people (2-3 sentences).
    
    RULES:
    - No jargon (no OSINT, telemetry, bilaterals, tactical, reconnaissance, frontier, strategic, etc.).
    - No filler words, no first-person, no opinions.
    - Keep the total response under 200 words.
    """
    
    summary = ""
    if settings.llm_provider == "ollama" and settings.ollama_base_url:
        try:
            summary = await call_ollama(prompt, "You are a clear and simple writer.")
        except Exception as exc:
            logger.warning("[Summarizer] Ollama call failed, falling back: %s", exc)
    if not summary:
        if settings.openai_api_key:
            summary = await call_openai(prompt, "You are a clear and simple writer.")
        elif settings.google_api_key:
            summary = await call_gemini(prompt, "You are a clear and simple writer.")
        else:
            summary = _local_summary(articles, timeframe)
            
    return summary

