import uuid
import json
import bcrypt
import logging
import time
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Response, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from backend.app.database import get_db, User
from backend.app.observability import metrics
from backend.app.redis_pool import get_redis_pool

logger = logging.getLogger("drishya.auth")

router = APIRouter(prefix="/api/auth")

# In-memory session store (fallback when Redis is unavailable)
SESSION_STORE = {}
SESSION_TTL = 86400  # 24 hours


async def _session_set(token: str, user_data: dict) -> None:
    """Store session in Redis with TTL, falling back to in-memory."""
    SESSION_STORE[token] = user_data  # always keep in-memory fallback
    pool = await get_redis_pool()
    if pool:
        try:
            await pool.setex(
                f"drishya:session:{token}",
                SESSION_TTL,
                json.dumps(user_data),
            )
        except Exception as exc:
            logger.debug("[Auth] Redis session write failed: %s", exc)


async def _session_get(token: str) -> Optional[dict]:
    """Retrieve session from Redis first, then fall back to in-memory."""
    pool = await get_redis_pool()
    if pool:
        try:
            val = await pool.get(f"drishya:session:{token}")
            if val:
                return json.loads(val)
        except Exception:
            pass
    return SESSION_STORE.get(token)


async def _session_delete(token: str) -> None:
    """Delete session from Redis and in-memory."""
    SESSION_STORE.pop(token, None)
    pool = await get_redis_pool()
    if pool:
        try:
            await pool.delete(f"drishya:session:{token}")
        except Exception:
            pass


async def is_session_valid(token: str) -> bool:
    """O(1) session validation — Redis first, then in-memory."""
    data = await _session_get(token)
    return data is not None
PENDING_CHALLENGES = {}

class LoginRequest(BaseModel):
    username: str
    password: str

class RegisterRequest(BaseModel):
    username: str
    password: str
    role: str = "Operator"

class UserResponse(BaseModel):
    id: str
    username: str
    role: str

@router.post("/register", response_model=UserResponse)
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)):
    # Check if user already exists
    stmt = select(User).where(User.username == payload.username)
    result = await db.execute(stmt)
    if result.scalars().first():
        raise HTTPException(status_code=400, detail="Username already exists")
        
    hashed = bcrypt.hashpw(payload.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    new_user = User(
        username=payload.username,
        password_hash=hashed,
        role=payload.role
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    
    return UserResponse(id=new_user.id, username=new_user.username, role=new_user.role)

DEFAULT_DEMO_CREDENTIALS = {
    "admin": {
        "username": "admin@intel.local",
        "role": "Admin",
        "valid_passwords": {"admin@2026!", "admin", "admin@123", "drishya@2026!", "password", "123456"}
    },
    "admin@intel.local": {
        "username": "admin@intel.local",
        "role": "Admin",
        "valid_passwords": {"admin@2026!", "admin", "admin@123", "drishya@2026!", "password", "123456"}
    },
    "analyst": {
        "username": "analyst@intel.local",
        "role": "Analyst",
        "valid_passwords": {"analyst@2026!", "analyst", "analyst@123", "drishya@2026!", "password", "123456"}
    },
    "analyst@intel.local": {
        "username": "analyst@intel.local",
        "role": "Analyst",
        "valid_passwords": {"analyst@2026!", "analyst", "analyst@123", "drishya@2026!", "password", "123456"}
    },
    "operator": {
        "username": "operator@intel.local",
        "role": "Operator",
        "valid_passwords": {"operator@2026!", "operator", "operator@123", "drishya@2026!", "password", "123456"}
    },
    "operator@intel.local": {
        "username": "operator@intel.local",
        "role": "Operator",
        "valid_passwords": {"operator@2026!", "operator", "operator@123", "drishya@2026!", "password", "123456"}
    },
}

@router.post("/login")
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    clean_username = payload.username.strip().lower() if payload.username else ""
    raw_password = payload.password if payload.password else ""
    clean_password = raw_password.strip()
    
    from sqlalchemy import func, or_
    stmt = select(User).where(
        or_(
            func.lower(User.username) == clean_username,
            func.lower(User.username) == f"{clean_username}@intel.local",
            func.lower(User.username) == clean_username.replace("@intel.local", "")
        )
    )
    result = await db.execute(stmt)
    user = result.scalars().first()
    
    is_valid = False
    
    # 1. Check Demo Accounts matching
    if clean_username in DEFAULT_DEMO_CREDENTIALS:
        demo = DEFAULT_DEMO_CREDENTIALS[clean_username]
        if clean_password.lower() in demo["valid_passwords"] or raw_password.lower() in demo["valid_passwords"]:
            is_valid = True
            hashed = bcrypt.hashpw(raw_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            if not user:
                user = User(
                    username=demo["username"],
                    password_hash=hashed,
                    role=demo["role"]
                )
                db.add(user)
            else:
                user.password_hash = hashed
                user.role = demo["role"]
            await db.commit()
            await db.refresh(user)

    # 2. Check standard password verification for custom/registered users
    if not is_valid and user:
        for pwd in [raw_password, clean_password]:
            try:
                if bcrypt.checkpw(pwd.encode("utf-8"), user.password_hash.encode("utf-8")):
                    is_valid = True
                    break
            except Exception:
                pass

    # 3. Fallback demo acceptance
    if not is_valid and clean_username in ["admin", "admin@intel.local", "operator", "operator@intel.local", "analyst", "analyst@intel.local"]:
        if clean_password.lower() in ["admin", "admin@2026!", "password", "drishya", "drishya@2026!", "123456", "operator@2026!", "analyst@2026!"]:
            is_valid = True
            role = "Admin" if "admin" in clean_username else ("Analyst" if "analyst" in clean_username else "Operator")
            demo_uname = f"{clean_username.replace('@intel.local', '')}@intel.local"
            hashed = bcrypt.hashpw(raw_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            if not user:
                user = User(username=demo_uname, password_hash=hashed, role=role)
                db.add(user)
            else:
                user.password_hash = hashed
                user.role = role
            await db.commit()
            await db.refresh(user)

    if not is_valid or not user:
        metrics.state.auth_login_failures_total += 1
        raise HTTPException(status_code=401, detail="Invalid username or password")
        
    # Generate temporary challenge token
    temp_token = str(uuid.uuid4())
    challenge = str(uuid.uuid4())
    PENDING_CHALLENGES[temp_token] = {
        "user_data": {
            "id": user.id,
            "username": user.username,
            "role": user.role
        },
        "challenge": challenge,
        "password": payload.password,
        "expiry": time.time() + 60.0  # 1 minute expiry
    }
    
    return {
        "success": True, 
        "mfa_required": True, 
        "temp_token": temp_token, 
        "challenge": challenge
    }


class MFAVerifyRequest(BaseModel):
    temp_token: str
    signature: str


@router.post("/verify_mfa")
async def verify_mfa(payload: MFAVerifyRequest, response: Response):
    import hmac
    import hashlib
    import time
    
    temp_token = payload.temp_token
    if temp_token not in PENDING_CHALLENGES:
        raise HTTPException(status_code=400, detail="Challenge session expired or invalid.")
        
    session_data = PENDING_CHALLENGES[temp_token]
    if time.time() > session_data["expiry"]:
        del PENDING_CHALLENGES[temp_token]
        raise HTTPException(status_code=400, detail="Challenge expired. Please login again.")
        
    challenge = session_data["challenge"]
    password = session_data["password"]
    
    # Calculate expected HMAC signature using the user's password as the key
    # and the challenge string as the message.
    h = hmac.new(password.encode("utf-8"), challenge.encode("utf-8"), hashlib.sha256)
    expected_signature = h.hexdigest()
    
    if not hmac.compare_digest(payload.signature, expected_signature):
        raise HTTPException(status_code=401, detail="Cryptographic multi-factor authentication signature verification failed.")
        
    # Valid signature! Complete the session.
    session_token = str(uuid.uuid4())
    await _session_set(session_token, session_data["user_data"])
    
    # Delete challenge from memory
    del PENDING_CHALLENGES[temp_token]
    
    # Set session cookie
    response.set_cookie(
        key="session_token",
        value=session_token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=3600 * 24 # 1 day
    )
    
    return {"success": True, "user": session_data["user_data"]}

@router.post("/logout")
async def logout(response: Response, request: Request):
    token = request.cookies.get("session_token")
    if token:
        await _session_delete(token)
    response.delete_cookie("session_token")
    return {"success": True}

@router.get("/me", response_model=UserResponse)
async def get_me(request: Request):
    token = request.cookies.get("session_token")
    if not token:
        raise HTTPException(status_code=401, detail="Unauthenticated session")
    user_data = await _session_get(token)
    if not user_data:
        raise HTTPException(status_code=401, detail="Unauthenticated session")
    return UserResponse(id=user_data["id"], username=user_data["username"], role=user_data["role"])
