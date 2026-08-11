from datetime import datetime, timedelta, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from backend.app.database import get_db, SharedNote
from backend.app.api.routes.auth import SESSION_STORE

router = APIRouter(prefix="/api/notes")

class NoteResponse(BaseModel):
    id: str
    content: str
    author: str
    created_at: datetime

    class Config:
        from_attributes = True

class NoteCreate(BaseModel):
    content: str
    author: Optional[str] = None

@router.get("", response_model=List[NoteResponse])
async def get_active_notes(db: AsyncSession = Depends(get_db)):
    """
    Retrieve shared notes created in the last 24 hours.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    stmt = select(SharedNote).where(SharedNote.created_at >= cutoff).order_by(SharedNote.created_at.desc())
    result = await db.execute(stmt)
    return result.scalars().all()

@router.post("", response_model=NoteResponse)
async def create_note(payload: NoteCreate, request: Request, db: AsyncSession = Depends(get_db)):
    """
    Create a new shared note. Resolves logged-in user if author is not provided.
    """
    if not payload.content.strip():
        raise HTTPException(status_code=400, detail="Note content cannot be empty.")
    
    author = payload.author
    if not author:
        token = request.cookies.get("session_token")
        if token and token in SESSION_STORE:
            author = SESSION_STORE[token].get("username")
        else:
            author = "Strategic Command"

    note = SharedNote(
        content=payload.content.strip(),
        author=author
    )
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return note

@router.delete("/{note_id}")
async def delete_note(note_id: str, db: AsyncSession = Depends(get_db)):
    """
    Delete a note manually.
    """
    stmt = select(SharedNote).where(SharedNote.id == note_id)
    result = await db.execute(stmt)
    note = result.scalars().first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    
    await db.delete(note)
    await db.commit()
    return {"status": "deleted"}
