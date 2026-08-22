from datetime import datetime, timedelta, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, ConfigDict

from backend.app.database import get_db, SharedNote, NoteVersion
from backend.app.api.routes.auth import SESSION_STORE

router = APIRouter(prefix="/api/notes")

class NoteResponse(BaseModel):
    id: str
    content: str
    author: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class NoteCreate(BaseModel):
    content: str
    author: Optional[str] = None


class NoteVersionResponse(BaseModel):
    id: str
    note_id: str
    content: str
    author: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

@router.get("", response_model=List[NoteResponse])
async def get_active_notes(q: Optional[str] = Query(None), db: AsyncSession = Depends(get_db)):
    """
    Retrieve shared notes created in the last 24 hours.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    stmt = select(SharedNote).where(SharedNote.created_at >= cutoff).order_by(SharedNote.created_at.desc())
    if q and q.strip():
        stmt = stmt.where(SharedNote.content.ilike(f"%{q.strip()}%"))
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
    db.add(NoteVersion(note_id=note.id, content=note.content, author=note.author))
    await db.commit()
    await db.refresh(note)
    return note


@router.put("/{note_id}", response_model=NoteResponse)
async def update_note(note_id: str, payload: NoteCreate, request: Request, db: AsyncSession = Depends(get_db)):
    if not payload.content.strip():
        raise HTTPException(status_code=400, detail="Note content cannot be empty.")
    result = await db.execute(select(SharedNote).where(SharedNote.id == note_id))
    note = result.scalars().first()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    author = payload.author or note.author
    token = request.cookies.get("session_token")
    if token and token in SESSION_STORE:
        author = SESSION_STORE[token].get("username") or author
    note.content = payload.content.strip()
    note.author = author
    db.add(NoteVersion(note_id=note.id, content=note.content, author=author))
    await db.commit()
    await db.refresh(note)
    return note


@router.get("/{note_id}/history", response_model=List[NoteVersionResponse])
async def get_note_history(note_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SharedNote).where(SharedNote.id == note_id))
    if not result.scalars().first():
        raise HTTPException(status_code=404, detail="Note not found")
    versions = await db.execute(
        select(NoteVersion).where(NoteVersion.note_id == note_id).order_by(NoteVersion.created_at.desc())
    )
    return versions.scalars().all()

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
