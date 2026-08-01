from __future__ import annotations

import asyncio
import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import settings
from backend.app.database import Article, ArchiveSummary

logger = logging.getLogger("drishya.summarizer")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def _redis_client():
    try:
        redis_conn = aioredis.from_url(settings.redis_url, decode_responses=True)
        await redis_conn.ping()
        return redis_conn
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
    by_dept: Dict[str, List[Article]] = {
        "Military & Defense": [],
        "Economic & Financial": [],
        "Social Affairs & Welfare": [],
        "Political & Diplomatic": [],
    }
    for article in articles:
        if article.department not in by_dept:
            by_dept[article.department] = []
        by_dept[article.department].append(article)

    markdown = f"# Executive OSINT Briefing ({timeframe})\n"
    markdown += f"Generated at: {_utc_now().isoformat()} (Heuristic Fallback)\n\n"
    for dept, grouped in by_dept.items():
        markdown += f"## {dept}\n"
        if not grouped:
            markdown += "*No high impact events recorded in this sector.*\n\n"
            continue
        markdown += f"*Total verified alerts: {len(grouped)}*\n\n"
        for index, article in enumerate(grouped[:5], start=1):
            summary_text = article.summary or article.content[:160] + "..."
            markdown += f"**{index}. [{article.title}]({article.url})**  \n"
            markdown += f"Source: {article.source} | Target: {article.country_code}  \n"
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


async def _compose_single_pass_summary(articles: List[Article], timeframe: str) -> str:
    article_context = "\n---\n".join(_article_block(article) for article in articles)
    prompt = f"""
You are a senior geopolitical intelligence analyst.
Write a cohesive executive briefing for the timeframe {timeframe}.
Use the articles below as the entire source set.
Return markdown with sections for Military & Defense, Economic & Financial, Social Affairs & Welfare, and Political & Diplomatic.
Keep it coherent and avoid repetitive phrasing.

Source articles:
{article_context}
"""
    if settings.openai_api_key:
        return await call_openai(prompt, "You are a senior strategic intelligence officer.")
    if settings.google_api_key:
        return await call_gemini(prompt, "You are a senior strategic intelligence officer.")
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
Combine these partial intelligence summaries into a single executive briefing for {timeframe}.
Preserve the strongest findings and remove repetition.

Partial summaries:
{chr(10).join(f'- {summary}' for summary in summaries)}
"""
    if settings.openai_api_key:
        return await call_openai(reduce_prompt, "You are a senior strategic intelligence officer.")
    if settings.google_api_key:
        return await call_gemini(reduce_prompt, "You are a senior strategic intelligence officer.")
    return _local_summary(articles, timeframe)


async def _read_cache(timeframe: str, version: str) -> Optional[str]:
    redis_conn = await _redis_client()
    if not redis_conn:
        return None
    cache_key = f"drishya:archive-summary:{timeframe}:{version}"
    cached = await redis_conn.get(cache_key)
    if cached:
        return cached
    return None


async def _write_cache(timeframe: str, version: str, summary: str) -> None:
    redis_conn = await _redis_client()
    if not redis_conn:
        return
    cache_key = f"drishya:archive-summary:{timeframe}:{version}"
    await redis_conn.set(cache_key, summary, ex=settings.archive_cache_ttl_seconds)


async def _get_archive_version() -> str:
    redis_conn = await _redis_client()
    if not redis_conn:
        return "0"
    version = await redis_conn.get("drishya:archive:version")
    return version or "0"


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
        summary = f"# Executive OSINT Briefing ({timeframe})\n\nNo high impact events detected within this archive window."
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
