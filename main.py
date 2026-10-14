import json
import time
from typing import List, Optional
from datetime import date, datetime

BATTERY_REFILL_DURATION = 15 * 60
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

class StoreBuyRequest(BaseModel):
    item_id: str

class CreateMistakeRequest(BaseModel):
    node_id: Optional[str] = ""
    lesson_title: Optional[str] = ""
    question_title: Optional[str] = ""
    code_snippet: Optional[str] = ""
    user_answer: Optional[str] = ""
    correct_answer: Optional[str] = ""
    explanation: Optional[str] = ""

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

def check_and_update_battery_timer(progress, db: Session):
    now = time.time()
    recharge_at = getattr(progress, "battery_recharge_at", None)
    if progress.battery == 0:
        if recharge_at is None:
            progress.battery_recharge_at = now + BATTERY_REFILL_DURATION
            db.commit()
            db.refresh(progress)
        elif now >= recharge_at:
            progress.battery = 5
            progress.battery_recharge_at = None
            db.commit()
            db.refresh(progress)
    elif progress.battery >= 5:
        if recharge_at is not None:
            progress.battery_recharge_at = None
            db.commit()
            db.refresh(progress)

def serialize_mistake(m: models.UserMistake):
    return {
        "id": m.id,
        "user_id": m.user_id,
        "node_id": m.node_id,
        "lesson_title": m.lesson_title,
        "question_title": m.question_title,
        "code_snippet": m.code_snippet,
        "user_answer": m.user_answer,
        "correct_answer": m.correct_answer,
        "explanation": m.explanation,
        "status": m.status,
        "created_at": m.created_at.isoformat() if m.created_at else None
    }

def ensure_seed_mistakes(db: Session, user_id: str):
    count = db.query(models.UserMistake).filter(
        models.UserMistake.user_id == user_id,
        models.UserMistake.status == "pending"
    ).count()
    if count == 0:
        seed_data = [
            models.UserMistake(
                user_id=user_id,
                node_id="js-1-1",
                lesson_title="Variables & Scope",
                question_title="Predict output of block-scoped variable",
                code_snippet="function checkScope() {\n  if (true) {\n    var a = 10;\n    let b = 20;\n  }\n  console.log(a, typeof b);\n}\ncheckScope();",
                user_answer="10 20",
                correct_answer="10 undefined",
                explanation="Variables declared with let are block-scoped and cannot be accessed outside the if block, whereas var is function-scoped.",
                status="pending"
            ),
            models.UserMistake(
                user_id=user_id,
                node_id="js-1-2",
                lesson_title="Equality & Coercion",
                question_title="Strict vs loose equality result",
                code_snippet="const listA = [1, 2];\nconst listB = [1, 2];\nconsole.log(listA == listB, listA === listB);",
                user_answer="true true",
                correct_answer="false false",
                explanation="Arrays in JavaScript are compared by reference, not by content. Two distinct array instances are never equal.",
                status="pending"
            ),
            models.UserMistake(
                user_id=user_id,
                node_id="js-1-3",
                lesson_title="Array Mutation",
                question_title="Array method return value",
                code_snippet="const numbers = [1, 2, 3];\nconst result = numbers.push(4);\nconsole.log(result);",
                user_answer="[1, 2, 3, 4]",
                correct_answer="4",
                explanation="Array.prototype.push modifies the array in place and returns the new length of the array, not the array itself.",
                status="pending"
            )
        ]
        db.add_all(seed_data)
        db.commit()

def get_progress_response(progress, db: Optional[Session] = None):
    mistakes_count = 0
    if db:
        try:
            ensure_seed_mistakes(db, progress.user_id)
            mistakes_count = db.query(models.UserMistake).filter(
                models.UserMistake.user_id == progress.user_id,
                models.UserMistake.status == "pending"
            ).count()
        except Exception:
            mistakes_count = 0

    return {
        "totalXP": progress.totalXP,
        "streak": progress.streak,
        "battery": progress.battery,
        "battery_recharge_at": getattr(progress, "battery_recharge_at", None),
        "bytes": progress.bytes,
        "streak_freeze": getattr(progress, "streak_freeze", 0) or 0,
        "active_course": progress.active_course,
        "completed_nodes": progress.completed_nodes,
        "unlocked_nodes": progress.unlocked_nodes,
        "daily_quests_date": progress.daily_quests_date,
        "daily_quests_state": progress.daily_quests_state,
        "mistakes_count": mistakes_count
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
    check_and_update_battery_timer(progress, db)
    ensure_daily_quests(progress, db)
    return get_progress_response(progress, db)

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
    check_and_update_battery_timer(progress, db)
    if progress.battery > 0:
        progress.battery -= 1
        if progress.battery == 0 and not progress.battery_recharge_at:
            progress.battery_recharge_at = time.time() + BATTERY_REFILL_DURATION
        db.commit()
        db.refresh(progress)
    return get_progress_response(progress)

@app.post("/progress/recharge-battery")
def recharge_battery(
    user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    progress = get_user_progress(db, user)
    check_and_update_battery_timer(progress, db)
    if progress.battery < 5:
        progress.battery = 5
        progress.battery_recharge_at = None
        db.commit()
        db.refresh(progress)
    return get_progress_response(progress)

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

@app.post("/api/store/buy")
@app.post("/store/buy")
def buy_store_item(
    req: StoreBuyRequest,
    user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    progress = get_user_progress(db, user)
    item_id = req.item_id.strip().lower()

    if item_id == "refill_battery":
        price = 100
        if progress.bytes < price:
            raise HTTPException(status_code=400, detail="Insufficient Bytes")
        if progress.battery >= 5:
            raise HTTPException(status_code=400, detail="Battery is already full")
        progress.bytes -= price
        progress.battery = 5
        progress.battery_recharge_at = None

    elif item_id == "streak_freeze":
        price = 200
        current_freezes = progress.streak_freeze or 0
        if progress.bytes < price:
            raise HTTPException(status_code=400, detail="Insufficient Bytes")
        if current_freezes >= 2:
            raise HTTPException(status_code=400, detail="Streak Freeze capacity reached (maximum 2)")
        progress.bytes -= price
        progress.streak_freeze = current_freezes + 1

    else:
        raise HTTPException(status_code=400, detail="Invalid store item")

    db.commit()
    db.refresh(progress)
    return get_progress_response(progress, db)

@app.get("/api/debug/mistakes")
@app.get("/debug/mistakes")
def get_user_mistakes(
    user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    user_id = f"user_{user.id}" if user else "default_user"
    ensure_seed_mistakes(db, user_id)
    items = db.query(models.UserMistake).filter(
        models.UserMistake.user_id == user_id,
        models.UserMistake.status == "pending"
    ).order_by(models.UserMistake.created_at.desc()).all()
    return {
        "mistakes": [serialize_mistake(m) for m in items],
        "count": len(items)
    }

@app.post("/api/debug/mistakes")
@app.post("/debug/mistakes")
def create_user_mistake(
    req: CreateMistakeRequest,
    user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    user_id = f"user_{user.id}" if user else "default_user"
    mistake = models.UserMistake(
        user_id=user_id,
        node_id=req.node_id or "",
        lesson_title=req.lesson_title or "",
        question_title=req.question_title or "",
        code_snippet=req.code_snippet or "",
        user_answer=req.user_answer or "",
        correct_answer=req.correct_answer or "",
        explanation=req.explanation or "",
        status="pending"
    )
    db.add(mistake)
    db.commit()
    db.refresh(mistake)
    return serialize_mistake(mistake)

@app.post("/api/debug/mistakes/{mistake_id}/resolve")
@app.post("/debug/mistakes/{mistake_id}/resolve")
def resolve_user_mistake(
    mistake_id: int,
    user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    user_id = f"user_{user.id}" if user else "default_user"
    mistake = db.query(models.UserMistake).filter(
        models.UserMistake.id == mistake_id,
        models.UserMistake.user_id == user_id
    ).first()
    if not mistake:
        raise HTTPException(status_code=404, detail="Mistake not found")
    mistake.status = "resolved"
    progress = get_user_progress(db, user)
    progress.bytes += 10
    progress.totalXP += 10
    db.commit()
    db.refresh(mistake)
    db.refresh(progress)
    remaining_count = db.query(models.UserMistake).filter(
        models.UserMistake.user_id == user_id,
        models.UserMistake.status == "pending"
    ).count()
    return {
        "success": True,
        "resolved_id": mistake_id,
        "remaining_count": remaining_count,
        "reward_bytes": 10,
        "progress": get_progress_response(progress, db)
    }

@app.get("/api/debug/stats")
@app.get("/debug/stats")
def get_debug_stats(
    user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    user_id = f"user_{user.id}" if user else "default_user"
    ensure_seed_mistakes(db, user_id)
    pending_count = db.query(models.UserMistake).filter(
        models.UserMistake.user_id == user_id,
        models.UserMistake.status == "pending"
    ).count()
    resolved_count = db.query(models.UserMistake).filter(
        models.UserMistake.user_id == user_id,
        models.UserMistake.status == "resolved"
    ).count()
    return {
        "pending_mistakes_count": pending_count,
        "resolved_mistakes_count": resolved_count,
        "reading_exercises_count": 8,
        "refactor_exercises_count": 6,
        "stories_count": 4
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
