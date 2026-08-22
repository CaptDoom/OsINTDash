import json
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import AlertRule, Article, get_db
from backend.app.services.risk import calculate_country_risk

router = APIRouter(prefix="/api/alerts")


def datetime_now() -> datetime:
    return datetime.now(timezone.utc)


class AlertRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    keywords: List[str] = Field(default_factory=list, max_length=20)
    country_code: Optional[str] = Field(default=None, min_length=2, max_length=3)
    minimum_risk: Optional[str] = None
    channels: List[str] = Field(default_factory=lambda: ["in_app"], max_length=8)
    frequency: str = "immediate"


def serialize_rule(rule: AlertRule) -> dict:
    return {
        "id": rule.id,
        "name": rule.name,
        "keywords": json.loads(rule.keywords or "[]"),
        "country_code": rule.country_code,
        "minimum_risk": rule.minimum_risk,
        "channels": json.loads(rule.channels or "[\"in_app\"]"),
        "frequency": rule.frequency,
        "enabled": rule.enabled,
        "created_at": rule.created_at.isoformat(),
    }


@router.get("/rules")
async def list_alert_rules(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AlertRule).order_by(AlertRule.created_at.desc()))
    return [serialize_rule(rule) for rule in result.scalars().all()]


@router.post("/rules")
async def create_alert_rule(payload: AlertRuleCreate, db: AsyncSession = Depends(get_db)):
    allowed_risks = {"Low", "Moderate", "High", "Critical"}
    if payload.minimum_risk and payload.minimum_risk not in allowed_risks:
        raise HTTPException(status_code=400, detail="minimum_risk must be Low, Moderate, High, or Critical")
    rule = AlertRule(
        name=payload.name.strip(),
        keywords=json.dumps([keyword.strip() for keyword in payload.keywords if keyword.strip()]),
        country_code=payload.country_code.upper() if payload.country_code else None,
        minimum_risk=payload.minimum_risk,
        channels=json.dumps(payload.channels),
        frequency=payload.frequency,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return serialize_rule(rule)


@router.delete("/rules/{rule_id}")
async def delete_alert_rule(rule_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AlertRule).where(AlertRule.id == rule_id))
    rule = result.scalars().first()
    if not rule:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    await db.delete(rule)
    await db.commit()
    return {"status": "deleted"}


@router.get("/active")
async def evaluate_alert_rules(db: AsyncSession = Depends(get_db)):
    """Evaluate enabled rules against recent stored evidence for in-app delivery."""
    rules_result = await db.execute(select(AlertRule).where(AlertRule.enabled.is_(True)))
    articles_result = await db.execute(
        select(Article).where(Article.published_at >= datetime_now() - timedelta(days=7)).order_by(Article.published_at.desc()).limit(2000)
    )
    articles = articles_result.scalars().all()
    risk_order = {"Low": 0, "Moderate": 1, "High": 2, "Critical": 3}
    active = []
    for rule in rules_result.scalars().all():
        keywords = json.loads(rule.keywords or "[]")
        candidates = [article for article in articles if not rule.country_code or article.country_code == rule.country_code]
        matched = []
        for article in candidates:
            haystack = f"{article.title} {article.summary or ''} {article.content}".lower()
            if not keywords or any(keyword.lower() in haystack for keyword in keywords):
                matched.append(article)
        risk = calculate_country_risk(candidates)
        if rule.minimum_risk and risk_order[risk["level"]] < risk_order[rule.minimum_risk]:
            matched = []
        if matched:
            active.append({
                "rule": serialize_rule(rule),
                "matched_count": len(matched),
                "risk": risk,
                "articles": [{"id": article.id, "title": article.title, "url": article.url, "source": article.source} for article in matched[:20]],
            })
    return {"count": len(active), "alerts": active, "generated_at": datetime_now().isoformat()}
