from datetime import datetime, timedelta, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
import csv
import io
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, ConfigDict
from backend.app.database import get_db, Article
from backend.app.services.summarizer import generate_archive_summary, generate_archive_field_summary

router = APIRouter(prefix="/api/archive")

class ArticleResponse(BaseModel):
    id: str
    title: str
    headline: str
    summary: Optional[str]
    content: str
    url: str
    source: Optional[str]
    country_code: str
    published_at: datetime
    impact_level: str
    department: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class SummaryResponse(BaseModel):
    timeframe: str
    summary: str
    generated_at: datetime

@router.get("/{timeframe}", response_model=List[ArticleResponse])
async def get_archived_articles(
    timeframe: str,
    department: Optional[str] = Query(None, description="Filter by department (e.g. 'Military & Defense')"),
    country_code: Optional[str] = Query(None, min_length=2, max_length=3),
    source: Optional[str] = Query(None),
    impact_level: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="Search title, summary, content, or source"),
    limit: int = Query(500, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db)
):
    """
    Exposes timeframe archive lists filtering by '1M', '6M', '1Y'.
    """
    from datetime import timezone
    now = datetime.now(timezone.utc)
    if timeframe == "1M":
        start_date = now - timedelta(days=30)
    elif timeframe == "6M":
        start_date = now - timedelta(days=180)
    elif timeframe == "1Y":
        start_date = now - timedelta(days=365)
    else:
        raise HTTPException(status_code=400, detail="Invalid timeframe. Must be '1M', '6M', or '1Y'")

    stmt = select(Article)
    if department:
        stmt = stmt.where(Article.department == department)
    if country_code:
        stmt = stmt.where(Article.country_code == country_code.upper())
    if source:
        stmt = stmt.where(Article.source.ilike(f"%{source.strip()}%"))
    if impact_level:
        stmt = stmt.where(Article.impact_level == impact_level)
    if q and q.strip():
        term = f"%{q.strip()}%"
        stmt = stmt.where(
            Article.title.ilike(term)
            | Article.summary.ilike(term)
            | Article.content.ilike(term)
            | Article.source.ilike(term)
        )

    stmt_time = stmt.where(Article.published_at >= start_date)
    stmt_time = stmt_time.order_by(Article.published_at.desc()).limit(limit).offset(offset)
    
    result = await db.execute(stmt_time)
    articles = result.scalars().all()
    
    if not articles:
        # Fallback to ignore timeframe limit to ensure the archives are never empty
        stmt_all = stmt.order_by(Article.published_at.desc()).limit(limit).offset(offset)
        result = await db.execute(stmt_all)
        articles = result.scalars().all()
        
    return articles


@router.get("/export/{timeframe}")
async def export_archived_articles(
    timeframe: str,
    format: str = Query("json", pattern="^(json|csv)$"),
    department: Optional[str] = Query(None),
    country_code: Optional[str] = Query(None, min_length=2, max_length=3),
    db: AsyncSession = Depends(get_db),
):
    """Export archive evidence in machine-readable JSON or CSV form."""
    if timeframe not in ["1M", "6M", "1Y"]:
        raise HTTPException(status_code=400, detail="Invalid timeframe. Must be '1M', '6M', or '1Y'")
    now = datetime.now(timezone.utc)
    days = {"1M": 30, "6M": 180, "1Y": 365}[timeframe]
    stmt = select(Article).where(Article.published_at >= now - timedelta(days=days))
    if department:
        stmt = stmt.where(Article.department == department)
    if country_code:
        stmt = stmt.where(Article.country_code == country_code.upper())
    result = await db.execute(stmt.order_by(Article.published_at.desc()).limit(5000))
    articles = result.scalars().all()
    rows = [
        {
            "id": article.id,
            "title": article.title,
            "summary": article.summary or article.content[:300],
            "url": article.url,
            "source": article.source,
            "country_code": article.country_code,
            "published_at": article.published_at.isoformat(),
            "impact_level": article.impact_level,
            "department": article.department,
            "confidence_score": article.confidence_score,
        }
        for article in articles
    ]
    if format == "json":
        import json
        return Response(json.dumps(rows, default=str), media_type="application/json")
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()) if rows else ["id", "title", "summary", "url", "source", "country_code", "published_at", "impact_level", "department", "confidence_score"])
    writer.writeheader()
    writer.writerows(rows)
    return Response(buffer.getvalue(), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=drishya-{timeframe}.csv"})

@router.post("/summary/{timeframe}")
async def trigger_archive_summary(
    timeframe: str,
    department: Optional[str] = Query(None, description="Optional department filter"),
    db: AsyncSession = Depends(get_db)
):
    """
    Triggers or retrieves the summarization chain for the target archive timeframe and optional department.
    """
    if timeframe not in ["1M", "6M", "1Y"]:
        raise HTTPException(status_code=400, detail="Invalid timeframe. Must be '1M', '6M', or '1Y'")
        
    try:
        if department:
            # Generate real-time summary for a specific field
            summary_text = await generate_archive_field_summary(timeframe, department, db)
        else:
            # Generate general summary (uses cached map-reduce)
            summary_text = await generate_archive_summary(timeframe, db)
        return {"timeframe": timeframe, "department": department, "summary": summary_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Summarizer error: {str(e)}")
