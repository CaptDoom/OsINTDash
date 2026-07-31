from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from backend.app.database import get_db, Article
from backend.app.services.summarizer import generate_archive_summary

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

    class Config:
        from_attributes = True

class SummaryResponse(BaseModel):
    timeframe: str
    summary: str
    generated_at: datetime

@router.get("/{timeframe}", response_model=List[ArticleResponse])
async def get_archived_articles(
    timeframe: str,
    department: Optional[str] = Query(None, description="Filter by department (e.g. 'Military & Defense')"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db)
):
    """
    Exposes timeframe archive lists filtering by '1M', '6M', '1Y'.
    """
    now = datetime.utcnow()
    if timeframe == "1M":
        start_date = now - timedelta(days=30)
    elif timeframe == "6M":
        start_date = now - timedelta(days=180)
    elif timeframe == "1Y":
        start_date = now - timedelta(days=365)
    else:
        raise HTTPException(status_code=400, detail="Invalid timeframe. Must be '1M', '6M', or '1Y'")

    stmt = select(Article).where(
        Article.impact_level == "High Impact",
        Article.published_at >= start_date
    )

    if department:
        stmt = stmt.where(Article.department == department)

    stmt = stmt.order_by(Article.published_at.desc()).limit(limit).offset(offset)
    
    result = await db.execute(stmt)
    articles = result.scalars().all()
    return articles

@router.post("/summary/{timeframe}")
async def trigger_archive_summary(
    timeframe: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Triggers or retrieves the Map-Reduce summarization chain for the target archive timeframe.
    """
    if timeframe not in ["1M", "6M", "1Y"]:
        raise HTTPException(status_code=400, detail="Invalid timeframe. Must be '1M', '6M', or '1Y'")
        
    try:
        summary_text = await generate_archive_summary(timeframe, db)
        return {"timeframe": timeframe, "summary": summary_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Summarizer error: {str(e)}")
