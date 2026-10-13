import json
from typing import List, Optional
from datetime import date, datetime
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import or_

import models
import auth
from database import engine, get_db

models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Buggy Progress API (SQLAlchemy + Auth)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer(auto_error=False)

def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db)
) -> Optional[models.User]:
    if not credentials:
        return None
    token = credentials.credentials
    payload = auth.decode_access_token(token)
    if not payload:
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    try:
        return db.query(models.User).filter(models.User.id == int(user_id)).first()
    except Exception:
        return None

def get_current_user(
    user: Optional[models.User] = Depends(get_current_user_optional)
) -> models.User:
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required"
        )
    return user

class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str
    age: Optional[int] = None

class LoginRequest(BaseModel):
    login: str
    password: str

class CompleteNodeRequest(BaseModel):
    nodeId: str
    earnedXp: int
    nextNodeId: Optional[str] = None
    flawless: bool = False

class SwitchCourseRequest(BaseModel):
    courseId: str

class ByteTransactionRequest(BaseModel):
    amount: int

class ClaimQuestRequest(BaseModel):
    questId: str

def get_user_progress(db: Session, user: Optional[models.User] = None):
    user_id = f"user_{user.id}" if user else "default_user"
    progress = db.query(models.UserProgress).filter(models.UserProgress.user_id == user_id).first()
    if not progress:
        progress = models.UserProgress(
            user_id=user_id,
            battery=5,
            bytes=100,
            streak=0,
            totalXP=0,
            active_course="javascript"
        )
        db.add(progress)
        db.commit()
        db.refresh(progress)
    return progress

def ensure_daily_quests(progress, db):
    today_str = date.today().isoformat()
    quests = progress.daily_quests_state
    if progress.daily_quests_date != today_str or not quests or not isinstance(quests, list):
        progress.daily_quests_date = today_str
        quests = [
            {"id": "q1", "title": "Complete 1 lesson", "current": 0, "target": 1, "reward_bytes": 10, "claimed": False},
            {"id": "q2", "title": "Earn 30 XP", "current": 0, "target": 30, "reward_bytes": 15, "claimed": False},
            {"id": "q3", "title": "One flawless lesson", "current": 0, "target": 1, "reward_bytes": 25, "claimed": False}
        ]
        progress.daily_quests_state = quests
        db.commit()
        db.refresh(progress)
    return quests

def get_progress_response(progress):
    return {
        "totalXP": progress.totalXP,
        "streak": progress.streak,
        "battery": progress.battery,
        "bytes": progress.bytes,
        "active_course": progress.active_course,
        "completed_nodes": progress.completed_nodes,
        "unlocked_nodes": progress.unlocked_nodes,
        "daily_quests_date": progress.daily_quests_date,
        "daily_quests_state": progress.daily_quests_state
    }
def serialize_user(user: models.User):
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "age": user.age,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "avatar": user.avatar or {},
        "avatar_config": user.avatar_config or models.DEFAULT_AVATAR_CONFIG
    }

class UpdateAvatarConfigRequest(BaseModel):
    avatar_config: Optional[dict] = None
    skin_color: Optional[str] = None
    hair_style: Optional[str] = None
    hair_color: Optional[str] = None
    eye_style: Optional[str] = None
    expression: Optional[str] = None
    accessory: Optional[str] = None
    shirt_color: Optional[str] = None
    bg_color: Optional[str] = None

class UpdateAvatarRequest(BaseModel):
    avatar: Optional[dict] = None
    avatar_config: Optional[dict] = None

@app.post("/auth/register")
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    clean_username = req.username.strip()
    clean_email = req.email.strip().lower()

    if len(clean_username) < 3:
        raise HTTPException(status_code=400, detail="Username must be at least 3 characters")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    if req.age is not None and (req.age < 5 or req.age > 120):
        raise HTTPException(status_code=400, detail="Please enter a valid age")

    existing_user = db.query(models.User).filter(
        or_(models.User.username == clean_username, models.User.email == clean_email)
    ).first()
    if existing_user:
        if existing_user.username.lower() == clean_username.lower():
            raise HTTPException(status_code=400, detail="Username is already taken")
        raise HTTPException(status_code=400, detail="Email is already registered")

    hashed = auth.hash_password(req.password)
    user = models.User(
        username=clean_username,
        email=clean_email,
        hashed_password=hashed,
        age=req.age
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    progress = models.UserProgress(
        user_id=f"user_{user.id}",
        battery=5,
        bytes=100,
        streak=0,
        totalXP=0,
        active_course="javascript"
    )
    db.add(progress)
    db.commit()

    token = auth.create_access_token({
        "sub": str(user.id),
        "username": user.username,
        "email": user.email
    })

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": serialize_user(user)
    }

@app.post("/auth/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    login_val = req.login.strip().lower()
    user = db.query(models.User).filter(
        or_(models.User.email == login_val, models.User.username == req.login.strip())
    ).first()

    if not user or not auth.verify_password(req.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Incorrect username/email or password")

    token = auth.create_access_token({
        "sub": str(user.id),
        "username": user.username,
        "email": user.email
    })

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": serialize_user(user)
    }

@app.get("/auth/me")
@app.get("/api/user/profile")
@app.get("/user/profile")
def get_user_profile(user: models.User = Depends(get_current_user)):
    return serialize_user(user)

@app.put("/api/user/avatar")
@app.put("/user/avatar")
@app.post("/api/user/avatar")
@app.post("/user/avatar")
def update_avatar_config(
    req: UpdateAvatarConfigRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    current = user.avatar_config or models.DEFAULT_AVATAR_CONFIG.copy()
    if req.avatar_config and isinstance(req.avatar_config, dict):
        current.update(req.avatar_config)
    
    for field in ["skin_color", "hair_style", "hair_color", "eye_style", "expression", "accessory", "shirt_color", "bg_color"]:
        val = getattr(req, field)
        if val is not None:
            current[field] = val

    user.avatar_config = current
    db.commit()
    db.refresh(user)
    return {"user": serialize_user(user), "avatar_config": user.avatar_config}

@app.get("/progress")
def get_progress(
    user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    progress = get_user_progress(db, user)
    ensure_daily_quests(progress, db)
    return get_progress_response(progress)

@app.post("/progress/complete-node")
def complete_node(
    req: CompleteNodeRequest,
    user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    progress = get_user_progress(db, user)
    if req.earnedXp:
        progress.totalXP += req.earnedXp
    completed = progress.completed_nodes
    unlocked = progress.unlocked_nodes
    if req.nodeId and req.nodeId not in completed:
        progress.streak += 1
        completed.append(req.nodeId)
    if req.nextNodeId and req.nextNodeId not in unlocked:
        unlocked.append(req.nextNodeId)

    progress.completed_nodes = completed
    progress.unlocked_nodes = unlocked
    quests = ensure_daily_quests(progress, db)
    for q in quests:
        if q["id"] == "q1":
            q["current"] = min(q["current"] + 1, q["target"])
        elif q["id"] == "q2" and req.earnedXp:
            q["current"] = min(q["current"] + req.earnedXp, q["target"])
        elif q["id"] == "q3" and req.flawless:
            q["current"] = min(q["current"] + 1, q["target"])
            
    progress.daily_quests_state = quests

    db.commit()
    db.refresh(progress)
    return get_progress_response(progress)

@app.post("/progress/decrease-battery")
def decrease_battery(
    user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    progress = get_user_progress(db, user)
    if progress.battery > 0:
        progress.battery -= 1
        db.commit()
        db.refresh(progress)
    return {"battery": progress.battery}

@app.post("/progress/recharge-battery")
def recharge_battery(
    user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    progress = get_user_progress(db, user)
    if progress.battery < 5:
        progress.battery += 1
        db.commit()
        db.refresh(progress)
    return {"battery": progress.battery}

@app.post("/bytes/transaction")
def bytes_transaction(
    req: ByteTransactionRequest,
    user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    progress = get_user_progress(db, user)
    progress.bytes += req.amount
    db.commit()
    db.refresh(progress)
    return {"bytes": progress.bytes}

@app.post("/user/course")
def switch_course(
    req: SwitchCourseRequest,
    user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    progress = get_user_progress(db, user)
    progress.active_course = req.courseId
    db.commit()
    db.refresh(progress)
    return get_progress_response(progress)

@app.post("/quests/claim")
def claim_quest(
    req: ClaimQuestRequest,
    user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    progress = get_user_progress(db, user)
    quests = ensure_daily_quests(progress, db)
    
    for q in quests:
        if q["id"] == req.questId and not q["claimed"] and q["current"] >= q["target"]:
            q["claimed"] = True
            progress.bytes += q["reward_bytes"]
            break
            
    progress.daily_quests_state = quests
    db.commit()
    db.refresh(progress)
    return get_progress_response(progress)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
