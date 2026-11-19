import json
import time
from typing import List, Optional
from datetime import date, datetime, timedelta

BATTERY_REFILL_DURATION = 15 * 60
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import or_, text

import models
import auth
from database import engine, get_db, SessionLocal

models.Base.metadata.create_all(bind=engine)

def ensure_sqlite_columns():
    with engine.begin() as conn:
        user_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(users)")).fetchall()]
        if "coder_title" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN coder_title VARCHAR DEFAULT 'Frontend Developer'"))
        if "dev_card" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN dev_card TEXT DEFAULT '{}'"))
        prog_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(user_progress)")).fetchall()]
        if "dev_card" not in prog_cols:
            conn.execute(text("ALTER TABLE user_progress ADD COLUMN dev_card TEXT DEFAULT '{}'"))
        squad_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(squad_requests)")).fetchall()]
        if squad_cols:
            if "project_vibe" not in squad_cols:
                conn.execute(text("ALTER TABLE squad_requests ADD COLUMN project_vibe VARCHAR DEFAULT 'Portfolio Builder'"))
            if "contact_link" not in squad_cols:
                conn.execute(text("ALTER TABLE squad_requests ADD COLUMN contact_link VARCHAR DEFAULT ''"))

ensure_sqlite_columns()

app = FastAPI(title="Buggy Progress API (SQLAlchemy + Auth)")

@app.get("/")
def root_health():
    return {"status": "ok", "service": "Buggy Progress API", "docs": "/docs"}

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
    earnedXp: int = 0
    earnedBytes: Optional[int] = 0
    nextNodeId: Optional[str] = None
    flawless: bool = False
    accuracy: Optional[int] = None
    mode: Optional[str] = None

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
            season_xp=0,
            active_course="javascript",
            last_battery_update=datetime.utcnow()
        )
        db.add(progress)
        db.commit()
        db.refresh(progress)
    return progress

DAILY_QUEST_POOL = [
    {"id": "earn_xp", "title": "Earn 30 XP today", "target": 30, "reward_bytes": 10},
    {"id": "complete_nodes", "title": "Finish 2 lesson nodes", "target": 2, "reward_bytes": 15},
    {"id": "clean_accuracy", "title": "Score 80%+ in a lesson", "target": 1, "reward_bytes": 20},
    {"id": "hunt_bugs", "title": "Fix 3 bugs in Debug Lab", "target": 3, "reward_bytes": 15},
    {"id": "perfect_node", "title": "Finish 1 node with 0 mistakes", "target": 1, "reward_bytes": 25},
]

VALID_QUEST_IDS = {q["id"] for q in DAILY_QUEST_POOL}
QUEST_TITLE_BY_ID = {q["id"]: q["title"] for q in DAILY_QUEST_POOL}

def build_daily_quests_for_date(target_date: date) -> list:
    third_pool = [DAILY_QUEST_POOL[2], DAILY_QUEST_POOL[3], DAILY_QUEST_POOL[4]]
    third_quest = third_pool[target_date.toordinal() % len(third_pool)]
    selected = [DAILY_QUEST_POOL[0], DAILY_QUEST_POOL[1], third_quest]
    return [
        {
            "id": item["id"],
            "title": item["title"],
            "current": 0,
            "target": item["target"],
            "reward_bytes": item["reward_bytes"],
            "claimed": False,
        }
        for item in selected
    ]

def ensure_daily_quests(progress, db):
    today_obj = date.today()
    today_str = today_obj.isoformat()
    quests = progress.daily_quests_state
    has_legacy_ids = (
        not isinstance(quests, list)
        or len(quests) != 3
        or any(not isinstance(q, dict) or q.get("id") not in VALID_QUEST_IDS for q in quests)
    )
    if progress.daily_quests_date != today_str or not quests or has_legacy_ids:
        progress.daily_quests_date = today_str
        quests = build_daily_quests_for_date(today_obj)
        progress.daily_quests_state = quests
        if db:
            db.commit()
            db.refresh(progress)
        return quests

    title_updated = False
    normalized_quests = []
    for q in quests:
        q_copy = dict(q)
        canonical_title = QUEST_TITLE_BY_ID.get(q_copy.get("id"))
        if canonical_title and q_copy.get("title") != canonical_title:
            q_copy["title"] = canonical_title
            title_updated = True
        normalized_quests.append(q_copy)
    if title_updated:
        progress.daily_quests_state = normalized_quests
        if db:
            db.commit()
            db.refresh(progress)
        return normalized_quests
    return quests

def update_user_daily_quests(
    progress,
    db,
    earned_xp: int = 0,
    completed_lesson_node: bool = False,
    high_accuracy: bool = False,
    flawless_node: bool = False,
    bugs_fixed_delta: int = 0,
):
    quests = [dict(q) for q in ensure_daily_quests(progress, db)]
    for q in quests:
        qid = q.get("id")
        target = int(q.get("target", 1))
        curr = int(q.get("current", 0))
        if qid == "earn_xp" and earned_xp > 0:
            curr = min(curr + int(earned_xp), target)
        elif qid == "complete_nodes" and completed_lesson_node:
            curr = min(curr + 1, target)
        elif qid == "clean_accuracy" and high_accuracy:
            curr = min(curr + 1, target)
        elif qid == "perfect_node" and flawless_node:
            curr = min(curr + 1, target)
        elif qid == "hunt_bugs" and bugs_fixed_delta > 0:
            curr = min(curr + int(bugs_fixed_delta), target)
        q["current"] = curr
        if curr >= target and not q.get("claimed", False):
            q["claimed"] = True
            progress.bytes = (progress.bytes or 0) + int(q.get("reward_bytes", 0))
    progress.daily_quests_state = quests
    return quests

def sync_user_battery(progress: models.UserProgress, db: Optional[Session] = None) -> int:
    now = datetime.utcnow()
    changed = False
    if progress.battery < 5:
        if not progress.last_battery_update:
            progress.last_battery_update = now
            changed = True
            elapsed = 0.0
        else:
            elapsed = (now - progress.last_battery_update).total_seconds()
            if elapsed < 0:
                elapsed = 0.0

        units = int(elapsed // 1800)
        if units > 0:
            progress.battery = min(5, progress.battery + units)
            progress.last_battery_update = progress.last_battery_update + timedelta(seconds=units * 1800)
            changed = True

        if progress.battery >= 5:
            progress.battery = 5
            seconds_left = 0
        else:
            current_elapsed = (now - progress.last_battery_update).total_seconds()
            seconds_left = max(0, int(1800 - (current_elapsed % 1800)))
    else:
        if progress.battery > 5:
            progress.battery = 5
            changed = True
        progress.last_battery_update = now
        seconds_left = 0

    if changed and db:
        try:
            db.commit()
            db.refresh(progress)
        except Exception:
            pass

    return seconds_left

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
        "resolved": bool(m.resolved),
        "created_at": m.created_at.isoformat() if m.created_at else None
    }

def update_user_streak(progress) -> bool:
    today = date.today()
    last_date = progress.last_active_date
    if isinstance(last_date, str):
        try:
            last_date = datetime.strptime(last_date, "%Y-%m-%d").date()
        except Exception:
            last_date = None
    elif isinstance(last_date, datetime):
        last_date = last_date.date()

    if last_date == today:
        return False

    if last_date is None:
        progress.streak = max(1, progress.streak or 1)
        progress.last_active_date = today
        return True

    if last_date == today - timedelta(days=1):
        progress.streak = (progress.streak or 0) + 1
        progress.last_active_date = today
        return True

    freezes = getattr(progress, "streak_freeze", 0) or 0
    days_missed = (today - last_date).days - 1
    if freezes > 0 and days_missed <= freezes:
        progress.streak_freeze = freezes - days_missed
        progress.streak = (progress.streak or 0) + 1
        progress.last_active_date = today
        return True

    progress.streak = 1
    progress.last_active_date = today
    return True

COURSE_TRACKS = [
    {"id": "javascript", "title": "JavaScript", "shortLabel": "JS", "prefix": "js-", "totalNodes": 10},
    {"id": "python", "title": "Python", "shortLabel": "PY", "prefix": "py-", "totalNodes": 10},
    {"id": "typescript", "title": "TypeScript", "shortLabel": "TS", "prefix": "ts-", "totalNodes": 10},
    {"id": "react", "title": "React", "shortLabel": "React", "prefix": "react-", "totalNodes": 10},
    {"id": "cpp", "title": "C++", "shortLabel": "C++", "prefix": "cpp-", "totalNodes": 10},
    {"id": "go", "title": "Go", "shortLabel": "Go", "prefix": "go-", "totalNodes": 10},
    {"id": "rust", "title": "Rust", "shortLabel": "Rust", "prefix": "rust-", "totalNodes": 10},
    {"id": "html", "title": "HTML & CSS", "shortLabel": "HTML", "prefix": "html-", "totalNodes": 10},
]

def compute_courses_mastery(completed_nodes: Optional[list], active_course: Optional[str] = "javascript") -> list:
    completed_set = {str(n).strip().lower() for n in (completed_nodes or []) if n}
    active_id = (active_course or "javascript").strip().lower()
    results = []
    for track in COURSE_TRACKS:
        done_count = sum(1 for node_id in completed_set if node_id.startswith(track["prefix"]))
        if done_count > 0 or track["id"] == active_id:
            total = max(1, int(track["totalNodes"]))
            percent = min(100, int(round((done_count / total) * 100)))
            results.append({
                "id": track["id"],
                "title": track["title"],
                "shortLabel": track["shortLabel"],
                "completedNodes": done_count,
                "totalNodes": total,
                "percent": percent,
                "isCompleted": percent >= 100,
            })
    if not results:
        results.append({
            "id": "javascript",
            "title": "JavaScript",
            "shortLabel": "JS",
            "completedNodes": 0,
            "totalNodes": 10,
            "percent": 0,
            "isCompleted": False,
        })
    results.sort(key=lambda item: (0 if item["id"] == "javascript" else 1, -item["percent"], item["title"]))
    return results

def compute_user_dev_stats(db: Optional[Session], target_uid_str: str, prog: Optional[models.UserProgress]) -> dict:
    streak = int(prog.streak or 0) if prog else 0
    completed_list = list(prog.completed_nodes or []) if prog else []
    completed_nodes_count = len(completed_list)
    active_course = (prog.active_course if prog and prog.active_course else "javascript")
    pending_mistakes_count = 0
    solved_mistakes_count = 0
    if db:
        try:
            pending_mistakes_count = db.query(models.UserMistake).filter(
                models.UserMistake.user_id == target_uid_str,
                models.UserMistake.resolved != True,
                models.UserMistake.status != "resolved"
            ).count()
            solved_mistakes_count = db.query(models.UserMistake).filter(
                models.UserMistake.user_id == target_uid_str,
                or_(models.UserMistake.resolved == True, models.UserMistake.status == "resolved")
            ).count()
        except Exception:
            pending_mistakes_count = 0
            solved_mistakes_count = 0

    total_mistakes_count = pending_mistakes_count + solved_mistakes_count
    bugs_fixed = solved_mistakes_count + completed_nodes_count

    if completed_nodes_count == 0 and total_mistakes_count == 0:
        accuracy = 0
    elif completed_nodes_count == 0:
        accuracy = min(100, int(round((solved_mistakes_count * 75) / max(1, total_mistakes_count))))
    else:
        base_steps = completed_nodes_count * 4
        earned_steps = base_steps + (solved_mistakes_count * 0.75)
        total_attempts = base_steps + total_mistakes_count
        accuracy = min(100, max(0, int(round((earned_steps / max(1, total_attempts)) * 100))))

    courses_mastery = compute_courses_mastery(completed_list, active_course)
    primary_mastery = courses_mastery[0]["percent"] if courses_mastery else 0

    return {
        "streak": streak,
        "bugs_fixed": bugs_fixed,
        "accuracy": accuracy,
        "mastery_percent": primary_mastery,
        "completed_nodes_count": completed_nodes_count,
        "solved_mistakes_count": solved_mistakes_count,
        "pending_mistakes_count": pending_mistakes_count,
        "courses_mastery": courses_mastery,
    }

def get_progress_response(progress, db: Optional[Session] = None, streak_increased: bool = False):
    seconds_left = sync_user_battery(progress, db)
    ensure_daily_quests(progress, db)
    dev_stats = compute_user_dev_stats(db, progress.user_id, progress)
    mistakes_count = dev_stats["pending_mistakes_count"]

    return {
        "totalXP": progress.totalXP,
        "season_xp": getattr(progress, "season_xp", 0) or 0,
        "streak": progress.streak,
        "streak_increased": streak_increased,
        "last_active_date": progress.last_active_date.isoformat() if progress.last_active_date else None,
        "battery": progress.battery,
        "max_battery": 5,
        "seconds_until_next_refill": seconds_left,
        "unresolved_mistakes_count": mistakes_count,
        "mistakes_count": mistakes_count,
        "solved_mistakes_count": dev_stats["solved_mistakes_count"],
        "bugs_fixed": dev_stats["bugs_fixed"],
        "accuracy": dev_stats["accuracy"],
        "mastery_percent": dev_stats["mastery_percent"],
        "courses_mastery": dev_stats["courses_mastery"],
        "battery_recharge_at": getattr(progress, "battery_recharge_at", None),
        "bytes": progress.bytes,
        "streak_freeze": getattr(progress, "streak_freeze", 0) or 0,
        "active_course": progress.active_course,
        "completed_nodes": progress.completed_nodes,
        "unlocked_nodes": progress.unlocked_nodes,
        "quest_date": progress.daily_quests_date,
        "daily_quests_date": progress.daily_quests_date,
        "daily_quests": progress.daily_quests_state,
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
        "avatar_config": user.avatar_config or models.DEFAULT_AVATAR_CONFIG,
        "coder_title": getattr(user, "coder_title", None) or "Frontend Developer",
        "dev_card": getattr(user, "dev_card", {}) or {}
    }

class UpdateDevCardRequest(BaseModel):
    specialtyTitle: Optional[str] = None
    collabStatus: Optional[str] = None
    bio: Optional[str] = None
    techStack: Optional[list] = None
    projects: Optional[list] = None
    githubUrl: Optional[str] = None
    linkedinUrl: Optional[str] = None
    websiteUrl: Optional[str] = None
    telegramUrl: Optional[str] = None
    email: Optional[str] = None

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

class UpdateUsernameRequest(BaseModel):
    username: str

class UpdateEmailRequest(BaseModel):
    email: str

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

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

    target_prog_uid = f"user_{user.id}"
    existing_prog = db.query(models.UserProgress).filter(models.UserProgress.user_id == target_prog_uid).first()
    if existing_prog:
        existing_prog.battery = 5
        existing_prog.bytes = 100
        existing_prog.streak = 0
        existing_prog.totalXP = 0
        existing_prog.season_xp = 0
        existing_prog.active_course = "javascript"
        existing_prog.completed_nodes = []
        existing_prog.unlocked_nodes = ["js-1-1"]
        existing_prog.dev_card = {}
        existing_prog.last_battery_update = datetime.utcnow()
        db.commit()
    else:
        progress = models.UserProgress(
            user_id=target_prog_uid,
            battery=5,
            bytes=100,
            streak=0,
            totalXP=0,
            season_xp=0,
            active_course="javascript",
            last_battery_update=datetime.utcnow()
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

@app.put("/api/user/username")
@app.put("/user/username")
@app.post("/api/user/username")
@app.post("/user/username")
def update_username(
    req: UpdateUsernameRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    clean = req.username.strip()
    if len(clean) < 3:
        raise HTTPException(status_code=400, detail="Username must be at least 3 characters")
    if len(clean) > 30:
        raise HTTPException(status_code=400, detail="Username must be at most 30 characters")

    existing = db.query(models.User).filter(
        models.User.username.ilike(clean),
        models.User.id != user.id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="This username is already taken")

    user.username = clean
    db.commit()
    db.refresh(user)
    return {"success": True, "user": serialize_user(user)}

@app.put("/api/user/email")
@app.put("/user/email")
@app.post("/api/user/email")
@app.post("/user/email")
def update_email(
    req: UpdateEmailRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    clean = req.email.strip().lower()
    if "@" not in clean or "." not in clean:
        raise HTTPException(status_code=400, detail="Please enter a valid email address")
    if len(clean) > 100:
        raise HTTPException(status_code=400, detail="Email is too long")

    existing = db.query(models.User).filter(
        models.User.email == clean,
        models.User.id != user.id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="This email is already in use")

    user.email = clean
    db.commit()
    db.refresh(user)
    return {"success": True, "user": serialize_user(user)}

@app.put("/api/user/password")
@app.put("/user/password")
@app.post("/api/user/password")
@app.post("/user/password")
def change_password(
    req: ChangePasswordRequest,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not auth.verify_password(req.current_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters")

    user.hashed_password = auth.hash_password(req.new_password)
    db.commit()
    db.refresh(user)
    return {"success": True, "message": "Password changed successfully"}

@app.get("/api/user/dev-card")
@app.get("/user/dev-card")
def get_dev_card(
    user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    progress = get_user_progress(db, user)
    target_uid_str = f"user_{user.id}" if user else "default_user"
    dev_stats = compute_user_dev_stats(db, target_uid_str, progress)
    if user:
        card_data = dict(user.dev_card or {})
        coder_title = getattr(user, "coder_title", None) or card_data.get("specialtyTitle") or "Full-Stack Developer"
        return {
            "is_authenticated": True,
            "username": user.username,
            "avatar_config": user.avatar_config or models.DEFAULT_AVATAR_CONFIG.copy(),
            "coder_title": coder_title,
            "dev_card": card_data,
            "stats": dev_stats,
            "courses_mastery": dev_stats["courses_mastery"],
        }
    guest_card = dict(progress.dev_card or {})
    guest_title = guest_card.get("specialtyTitle") or "Full-Stack Developer"
    return {
        "is_authenticated": False,
        "username": "Developer",
        "avatar_config": models.DEFAULT_AVATAR_CONFIG.copy(),
        "coder_title": guest_title,
        "dev_card": guest_card,
        "stats": dev_stats,
        "courses_mastery": dev_stats["courses_mastery"],
    }

@app.put("/api/user/dev-card")
@app.put("/user/dev-card")
@app.post("/api/user/dev-card")
@app.post("/user/dev-card")
def update_dev_card(
    req: UpdateDevCardRequest,
    user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    progress = get_user_progress(db, user)
    target_uid_str = f"user_{user.id}" if user else "default_user"
    card_dict = req.dict(exclude_unset=True)

    if user:
        current_card = dict(user.dev_card or {})
        current_card.update(card_dict)
        if req.specialtyTitle is not None and req.specialtyTitle.strip():
            clean_title = req.specialtyTitle.strip()
            user.coder_title = clean_title
            current_card["specialtyTitle"] = clean_title
        user.dev_card = current_card
        db.commit()
        db.refresh(user)
        sync_user_projects_to_showcase(db, target_uid_str, user.username, current_card)
        dev_stats = compute_user_dev_stats(db, target_uid_str, progress)
        return {
            "success": True,
            "user": serialize_user(user),
            "dev_card": user.dev_card,
            "coder_title": user.coder_title,
            "stats": dev_stats,
            "courses_mastery": dev_stats["courses_mastery"],
        }

    guest_card = dict(progress.dev_card or {})
    guest_card.update(card_dict)
    if req.specialtyTitle is not None and req.specialtyTitle.strip():
        guest_card["specialtyTitle"] = req.specialtyTitle.strip()
    progress.dev_card = guest_card
    db.commit()
    db.refresh(progress)
    sync_user_projects_to_showcase(db, target_uid_str, "Developer", guest_card)
    dev_stats = compute_user_dev_stats(db, target_uid_str, progress)
    return {
        "success": True,
        "user": None,
        "dev_card": progress.dev_card,
        "coder_title": guest_card.get("specialtyTitle") or "Full-Stack Developer",
        "stats": dev_stats,
        "courses_mastery": dev_stats["courses_mastery"],
    }

@app.get("/progress")
@app.get("/api/user/progress")
@app.get("/user/progress")
def get_progress(
    user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    progress = get_user_progress(db, user)
    sync_user_battery(progress, db)
    ensure_daily_quests(progress, db)
    return get_progress_response(progress, db)

@app.post("/progress/complete-node")
def complete_node(
    req: CompleteNodeRequest,
    user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    progress = get_user_progress(db, user)
    sync_user_battery(progress, db)

    if req.earnedXp:
        progress.totalXP += req.earnedXp
        progress.season_xp = (getattr(progress, "season_xp", 0) or 0) + req.earnedXp

    if req.earnedBytes:
        progress.bytes += req.earnedBytes
    elif req.mode == "debug_stories":
        progress.bytes += 30
    elif req.mode == "debug_refactor":
        progress.bytes += 15
    elif req.mode == "debug_tracing":
        progress.bytes += 10
    elif not req.mode or not req.mode.startswith("debug"):
        progress.bytes += 10

    completed = list(progress.completed_nodes or [])
    unlocked = list(progress.unlocked_nodes or [])

    if req.nodeId and not (req.mode and req.mode.startswith("debug")):
        if req.nodeId not in completed:
            completed.append(req.nodeId)
        if req.nextNodeId and req.nextNodeId not in unlocked:
            unlocked.append(req.nextNodeId)

    progress.completed_nodes = completed
    progress.unlocked_nodes = unlocked

    streak_increased = update_user_streak(progress)

    is_debug_mode = bool(req.mode and req.mode.startswith("debug"))
    is_high_acc = bool(req.flawless or (req.accuracy is not None and req.accuracy >= 80))
    update_user_daily_quests(
        progress,
        db,
        earned_xp=int(req.earnedXp or 0),
        completed_lesson_node=bool(req.nodeId and not is_debug_mode),
        high_accuracy=is_high_acc,
        flawless_node=bool(req.flawless and not is_debug_mode),
        bugs_fixed_delta=1 if is_debug_mode else 0,
    )

    db.commit()
    db.refresh(progress)
    return get_progress_response(progress, db, streak_increased=streak_increased)

@app.post("/progress/decrease-battery")
def decrease_battery(
    user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    progress = get_user_progress(db, user)
    sync_user_battery(progress, db)
    if progress.battery > 0:
        if progress.battery == 5 or not progress.last_battery_update:
            progress.last_battery_update = datetime.utcnow()
        progress.battery -= 1
        db.commit()
        db.refresh(progress)
    return get_progress_response(progress, db)

@app.post("/progress/recharge-battery")
def recharge_battery(
    user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    progress = get_user_progress(db, user)
    progress.battery = 5
    progress.last_battery_update = datetime.utcnow()
    progress.battery_recharge_at = None
    db.commit()
    db.refresh(progress)
    return get_progress_response(progress, db)

@app.post("/progress/simulate-new-day")
@app.post("/api/dev/simulate-new-day")
def simulate_new_day(
    user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    progress = get_user_progress(db, user)
    progress.last_active_date = date.today() - timedelta(days=1)
    progress.daily_quests_date = (date.today() - timedelta(days=1)).isoformat()
    progress.battery = 5
    db.commit()
    db.refresh(progress)
    return get_progress_response(progress, db)

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
    return get_progress_response(progress, db)

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
    return get_progress_response(progress, db)

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
        progress.last_battery_update = datetime.utcnow()
        progress.battery_recharge_at = None

    elif item_id == "streak_freeze":
        price = 150
        current_freezes = progress.streak_freeze or 0
        if current_freezes >= 2:
            raise HTTPException(status_code=400, detail="Monthly limit of 2 Streak Freezes reached")
        if progress.bytes < price:
            raise HTTPException(status_code=400, detail="Insufficient Bytes")
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
    items = db.query(models.UserMistake).filter(
        models.UserMistake.user_id == user_id,
        models.UserMistake.resolved != True,
        models.UserMistake.status != "resolved"
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
    existing = db.query(models.UserMistake).filter(
        models.UserMistake.user_id == user_id,
        models.UserMistake.node_id == (req.node_id or ""),
        models.UserMistake.question_title == (req.question_title or ""),
        models.UserMistake.resolved != True,
        models.UserMistake.status != "resolved"
    ).first()
    if existing:
        existing.user_answer = req.user_answer or existing.user_answer
        existing.code_snippet = req.code_snippet or existing.code_snippet
        existing.correct_answer = req.correct_answer or existing.correct_answer
        existing.explanation = req.explanation or existing.explanation
        db.commit()
        db.refresh(existing)
        return serialize_mistake(existing)

    mistake = models.UserMistake(
        user_id=user_id,
        node_id=req.node_id or "",
        lesson_title=req.lesson_title or "",
        question_title=req.question_title or "",
        code_snippet=req.code_snippet or "",
        user_answer=req.user_answer or "",
        correct_answer=req.correct_answer or "",
        explanation=req.explanation or "",
        status="pending",
        resolved=False
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
    mistake.resolved = True
    progress = get_user_progress(db, user)
    progress.bytes += 10
    progress.totalXP += 10
    progress.season_xp = (getattr(progress, "season_xp", 0) or 0) + 10
    update_user_daily_quests(
        progress,
        db,
        earned_xp=10,
        completed_lesson_node=False,
        high_accuracy=False,
        flawless_node=False,
        bugs_fixed_delta=1,
    )
    db.commit()
    db.refresh(mistake)
    db.refresh(progress)
    remaining_count = db.query(models.UserMistake).filter(
        models.UserMistake.user_id == user_id,
        models.UserMistake.resolved != True,
        models.UserMistake.status != "resolved"
    ).count()
    return {
        "success": True,
        "resolved_id": mistake_id,
        "remaining_count": remaining_count,
        "reward_bytes": 10,
        "progress": get_progress_response(progress, db)
    }

INITIAL_DEBUG_CHALLENGES = {
    "output_lab": [
        {
            "id": "out_1",
            "code": "let x = 10;\nx += 5;\nconsole.log(x);",
            "options": ["10", "15", "5", "Error"],
            "answer": "15",
            "explanation": "x += 5 adds 5 to 10, so x becomes 15."
        },
        {
            "id": "out_2",
            "code": "console.log(\"5\" + 2);",
            "options": ["7", "\"52\"", "5", "NaN"],
            "answer": "\"52\"",
            "explanation": "Adding a string and a number joins them together as text."
        },
        {
            "id": "out_3",
            "code": "const items = [\"js\", \"py\", \"css\"];\nconsole.log(items[1]);",
            "options": ["\"js\"", "\"py\"", "\"css\"", "undefined"],
            "answer": "\"py\"",
            "explanation": "Array index starts at 0, so index 1 is the second item."
        },
        {
            "id": "out_4",
            "code": "let isReady = false;\nconsole.log(!isReady);",
            "options": ["false", "true", "null", "undefined"],
            "answer": "true",
            "explanation": "The ! operator flips false to true."
        },
        {
            "id": "out_5",
            "code": "const nums = [2, 4, 6];\nconst res = nums.map(n => n * 2);\nconsole.log(res[0]);",
            "options": ["2", "4", "6", "12"],
            "answer": "4",
            "explanation": "map() doubles every item, so the first item becomes 4."
        },
        {
            "id": "out_6",
            "code": "let count = 0;\nfor (let i = 0; i < 3; i++) {\n  count++;\n}\nconsole.log(count);",
            "options": ["2", "3", "4", "0"],
            "answer": "3",
            "explanation": "The loop runs 3 times (i = 0, 1, 2), increasing count to 3."
        },
        {
            "id": "out_7",
            "code": "const user = { name: \"Sara\" };\nconsole.log(user.age ?? 18);",
            "options": ["undefined", "null", "18", "0"],
            "answer": "18",
            "explanation": "The ?? operator returns 18 because user.age is undefined."
        },
        {
            "id": "out_8",
            "code": "const word = \"Buggy\";\nconsole.log(word.slice(0, 3));",
            "options": ["\"Bug\"", "\"Bugg\"", "\"ug\"", "\"ggy\""],
            "answer": "\"Bug\"",
            "explanation": "slice(0, 3) takes characters from index 0 up to 2."
        }
    ],
    "clean_code": [
        {
            "id": "clean_1",
            "messy_code": "const msg = \"Hello \" + name + \"!\";",
            "prompt": "Make this string cleaner with template literals:",
            "options": [
                "const msg = `Hello ${name}!`;",
                "const msg = 'Hello ${name}!';",
                "const msg = `Hello $name!`;"
            ],
            "answer": "const msg = `Hello ${name}!`;",
            "explanation": "Backticks (`) let you put variables directly inside strings."
        },
        {
            "id": "clean_2",
            "messy_code": "if (isLoggedIn === true) {\n  return true;\n} else {\n  return false;\n}",
            "prompt": "Simplify this redundant boolean check:",
            "options": [
                "return isLoggedIn;",
                "return isLoggedIn == true;",
                "if (isLoggedIn) return;"
            ],
            "answer": "return isLoggedIn;",
            "explanation": "isLoggedIn is already a boolean, so you can return it directly."
        },
        {
            "id": "clean_3",
            "messy_code": "const title = post.title;\nconst author = post.author;",
            "prompt": "Use object destructuring in one line:",
            "options": [
                "const { title, author } = post;",
                "const [ title, author ] = post;",
                "const post = { title, author };"
            ],
            "answer": "const { title, author } = post;",
            "explanation": "Object destructuring extracts multiple properties in one clean line."
        },
        {
            "id": "clean_4",
            "messy_code": "function double(n) {\n  return n * 2;\n}",
            "prompt": "Convert to a concise arrow function:",
            "options": [
                "const double = n => n * 2;",
                "const double = n => { n * 2 };",
                "arrow double(n) => n * 2;"
            ],
            "answer": "const double = n => n * 2;",
            "explanation": "Single-line arrow functions automatically return the expression."
        },
        {
            "id": "clean_5",
            "messy_code": "const all = listA.concat(listB);",
            "prompt": "Merge both arrays using the modern spread operator:",
            "options": [
                "const all = [...listA, ...listB];",
                "const all = [listA, listB];",
                "const all = { ...listA, ...listB };"
            ],
            "answer": "const all = [...listA, ...listB];",
            "explanation": "The spread operator (...) unpacks array items cleanly."
        },
        {
            "id": "clean_6",
            "messy_code": "let status;\nif (age >= 18) {\n  status = \"Adult\";\n} else {\n  status = \"Minor\";\n}",
            "prompt": "Use a single-line ternary operator:",
            "options": [
                "const status = age >= 18 ? \"Adult\" : \"Minor\";",
                "const status = age >= 18 : \"Adult\" ? \"Minor\";",
                "const status = if (age >= 18) \"Adult\" else \"Minor\";"
            ],
            "answer": "const status = age >= 18 ? \"Adult\" : \"Minor\";",
            "explanation": "Ternary operators make simple if/else assignments short and readable."
        },
        {
            "id": "clean_7",
            "messy_code": "if (user && user.profile && user.profile.avatar) {\n  show(user.profile.avatar);\n}",
            "prompt": "Safely read nested properties with optional chaining:",
            "options": [
                "if (user?.profile?.avatar) show(user.profile.avatar);",
                "if (user.profile?.avatar) show(user.avatar);",
                "if (user??profile??avatar) show(avatar);"
            ],
            "answer": "if (user?.profile?.avatar) show(user.profile.avatar);",
            "explanation": "Optional chaining (?.) stops safely if any parent object is missing."
        },
        {
            "id": "clean_8",
            "messy_code": "const active = [];\nfor (let i = 0; i < users.length; i++) {\n  if (users[i].isOnline) active.push(users[i]);\n}",
            "prompt": "Replace this loop with a built-in array method:",
            "options": [
                "const active = users.filter(u => u.isOnline);",
                "const active = users.map(u => u.isOnline);",
                "const active = users.find(u => u.isOnline);"
            ],
            "answer": "const active = users.filter(u => u.isOnline);",
            "explanation": "filter() returns a new array with only the items that pass the test."
        }
    ],
    "mini_scenarios": [
        {
            "id": "scen_1",
            "title": "Build a Shopping Cart Total",
            "subtitle": "Calculate prices and apply a discount code in 3 steps.",
            "steps": [
                {
                    "step": 1,
                    "instruction": "First, sum all item prices using reduce():",
                    "code_template": "const prices = [10, 20, 30];\nconst total = prices.___((sum, p) => sum + p, 0);",
                    "options": ["reduce", "map", "filter"],
                    "answer": "reduce"
                },
                {
                    "step": 2,
                    "instruction": "Check if the user has a valid promo code:",
                    "code_template": "const hasPromo = true;\nconst discount = hasPromo ___ 10 : 0;",
                    "options": ["?", "&&", "??"],
                    "answer": "?"
                },
                {
                    "step": 3,
                    "instruction": "Return the final price after discount:",
                    "code_template": "const finalPrice = total - discount;\n___ finalPrice;",
                    "options": ["return", "console.log", "break"],
                    "answer": "return"
                }
            ]
        },
        {
            "id": "scen_2",
            "title": "Password Strength Checker",
            "subtitle": "Validate a user password before form submit.",
            "steps": [
                {
                    "step": 1,
                    "instruction": "Remove extra spaces from both ends of the password:",
                    "code_template": "const rawPass = \"  secret123  \";\nconst cleanPass = rawPass.___();",
                    "options": ["trim", "slice", "split"],
                    "answer": "trim"
                },
                {
                    "step": 2,
                    "instruction": "Check if the password has at least 8 characters:",
                    "code_template": "const isLongEnough = cleanPass.___ >= 8;",
                    "options": ["length", "size", "count"],
                    "answer": "length"
                },
                {
                    "step": 3,
                    "instruction": "Make sure it contains a number or special symbol:",
                    "code_template": "const isValid = isLongEnough ___ cleanPass.includes(\"1\");",
                    "options": ["&&", "||", "!=="],
                    "answer": "&&"
                }
            ]
        },
        {
            "id": "scen_3",
            "title": "Fetch User Profile from API",
            "subtitle": "Load user data safely from a backend server.",
            "steps": [
                {
                    "step": 1,
                    "instruction": "Mark the function as asynchronous so we can use await:",
                    "code_template": "___ function loadUser() {\n  const res = await fetch(\"/api/user\");",
                    "options": ["async", "await", "defer"],
                    "answer": "async"
                },
                {
                    "step": 2,
                    "instruction": "Convert the server response into a JSON object:",
                    "code_template": "  const data = await res.___();\n  return data;",
                    "options": ["json", "parse", "text"],
                    "answer": "json"
                },
                {
                    "step": 3,
                    "instruction": "Catch network errors so the app never crashes:",
                    "code_template": "} ___ (error) {\n  console.log(\"Failed to load user\");\n}",
                    "options": ["catch", "else", "finally"],
                    "answer": "catch"
                }
            ]
        },
        {
            "id": "scen_4",
            "title": "Dark Mode Toggle Button",
            "subtitle": "Switch themes and save the choice in the browser.",
            "steps": [
                {
                    "step": 1,
                    "instruction": "Select the theme button from the DOM:",
                    "code_template": "const btn = document.___(\"" + chr(35) + "theme-btn\");",
                    "options": ["querySelector", "getElement", "select"],
                    "answer": "querySelector"
                },
                {
                    "step": 2,
                    "instruction": "Toggle the dark class on the page body when clicked:",
                    "code_template": "btn.addEventListener(\"click\", () => {\n  document.body.classList.___(\"dark\");\n});",
                    "options": ["toggle", "switch", "change"],
                    "answer": "toggle"
                },
                {
                    "step": 3,
                    "instruction": "Save the theme choice in localStorage:",
                    "code_template": "localStorage.___(\"theme\", \"dark\");",
                    "options": ["setItem", "save", "push"],
                    "answer": "setItem"
                }
            ]
        }
    ]
}

def ensure_debug_challenges_seed(db: Session):
    existing_rows = db.query(models.DebugChallenge).all()
    existing_map = {row.challenge_id: row for row in existing_rows}
    changed = False
    for category, items in INITIAL_DEBUG_CHALLENGES.items():
        for idx, item in enumerate(items):
            cid = item["id"]
            if cid not in existing_map:
                record = models.DebugChallenge(
                    challenge_id=cid,
                    category=category,
                    order_index=idx,
                )
                record.payload = item
                db.add(record)
                changed = True
            else:
                row = existing_map[cid]
                if row.category != category or row.order_index != idx or row.payload != item:
                    row.category = category
                    row.order_index = idx
                    row.payload = item
                    changed = True
    if changed:
        db.commit()

def seed_initial_debug_challenges_on_startup():
    db = SessionLocal()
    try:
        ensure_debug_challenges_seed(db)
    finally:
        db.close()

seed_initial_debug_challenges_on_startup()

@app.get("/api/debug/challenges")
@app.get("/debug/challenges")
def get_debug_challenges(db: Session = Depends(get_db)):
    ensure_debug_challenges_seed(db)
    rows = db.query(models.DebugChallenge).order_by(
        models.DebugChallenge.category.asc(),
        models.DebugChallenge.order_index.asc()
    ).all()
    grouped = {
        "output_lab": [],
        "clean_code": [],
        "mini_scenarios": []
    }
    for r in rows:
        if r.category in grouped:
            grouped[r.category].append(r.payload)
    return grouped

@app.get("/api/debug/stats")
@app.get("/debug/stats")
def get_debug_stats(
    user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    ensure_debug_challenges_seed(db)
    user_id = f"user_{user.id}" if user else "default_user"
    pending_count = db.query(models.UserMistake).filter(
        models.UserMistake.user_id == user_id,
        models.UserMistake.resolved != True,
        models.UserMistake.status != "resolved"
    ).count()
    resolved_count = db.query(models.UserMistake).filter(
        models.UserMistake.user_id == user_id,
        or_(models.UserMistake.resolved == True, models.UserMistake.status == "resolved")
    ).count()
    output_count = db.query(models.DebugChallenge).filter(
        models.DebugChallenge.category == "output_lab"
    ).count()
    clean_count = db.query(models.DebugChallenge).filter(
        models.DebugChallenge.category == "clean_code"
    ).count()
    scenarios_count = db.query(models.DebugChallenge).filter(
        models.DebugChallenge.category == "mini_scenarios"
    ).count()
    return {
        "pending_mistakes_count": pending_count,
        "resolved_mistakes_count": resolved_count,
        "reading_exercises_count": output_count or 8,
        "refactor_exercises_count": clean_count or 8,
        "stories_count": scenarios_count or 4
    }

LEAGUE_TITLES = [
    "Obsidian League",
    "Ruby League",
    "Diamond League",
    "Emerald League",
    "Grandmaster League",
]

def ensure_season(db: Session) -> models.SeasonInfo:
    season = db.query(models.SeasonInfo).first()
    now = datetime.utcnow()
    if not season:
        season = models.SeasonInfo(
            season_number=1,
            title="Current Compile Season",
            description="Competitive 10-day leaderboard season",
            end_time=now + timedelta(days=10),
            reward_first=200,
            reward_second=100,
            reward_third=50
        )
        db.add(season)
        db.commit()
        db.refresh(season)
        return season

    if now >= season.end_time:
        reward_and_reset_season(db, season)
    elif (season.end_time - now) > timedelta(days=10):
        season.end_time = now + timedelta(days=10)
        season.title = "Current Compile Season"
        db.commit()
        db.refresh(season)

    return season

def reward_and_reset_season(db: Session, season: models.SeasonInfo):
    now = datetime.utcnow()
    top_progs = db.query(models.UserProgress).order_by(models.UserProgress.season_xp.desc()).all()
    if len(top_progs) > 0:
        top_progs[0].bytes = (top_progs[0].bytes or 0) + (season.reward_first or 200)
    if len(top_progs) > 1:
        top_progs[1].bytes = (top_progs[1].bytes or 0) + (season.reward_second or 100)
    if len(top_progs) > 2:
        top_progs[2].bytes = (top_progs[2].bytes or 0) + (season.reward_third or 50)

    for p in db.query(models.UserProgress).all():
        p.season_xp = 0

    season.season_number = (season.season_number or 1) + 1
    season.title = "Current Compile Season"
    season.end_time = now + timedelta(days=10)
    db.commit()
    db.refresh(season)

def get_league_for_rank(rank: int) -> str:
    if rank <= 0:
        return "Unranked"
    if rank <= 3:
        return "Grandmaster League"
    if rank <= 10:
        return "Emerald League"
    if rank <= 25:
        return "Diamond League"
    if rank <= 50:
        return "Ruby League"
    return "Obsidian League"

def get_coder_title_for_rank(rank: int) -> str:
    if rank == 1:
        return "Senior"
    if rank == 2:
        return "Mid-Level"
    if rank == 3:
        return "Junior"
    if rank <= 5:
        return "Fullstack Ninja"
    if rank <= 10:
        return "Logic Maestro"
    if rank <= 15:
        return "Syntax Sorcerer"
    if rank <= 20:
        return "Async Wizard"
    return "Binary Overlord"

def compute_leaderboard_rankings(db: Session, user: Optional[models.User]):
    participants = []
    seen_usernames = set()

    all_users = db.query(models.User).all()
    for u in all_users:
        u_prog = db.query(models.UserProgress).filter(models.UserProgress.user_id == f"user_{u.id}").first()
        is_curr = bool(user and u.id == user.id)
        u_season_xp = (getattr(u_prog, "season_xp", 0) or 0) if u_prog else 0
        u_streak = (u_prog.streak or 0) if u_prog else 0
        u_avatar = u.avatar_config or models.DEFAULT_AVATAR_CONFIG.copy()
        u_title = getattr(u, "coder_title", None) or (u.dev_card.get("specialtyTitle") if getattr(u, "dev_card", None) else None) or "Frontend Developer"
        participants.append({
            "id": f"user_{u.id}",
            "username": u.username,
            "coder_title": u_title,
            "season_xp": u_season_xp,
            "streak": u_streak,
            "avatar_config": u_avatar,
            "is_current_user": is_curr,
        })
        seen_usernames.add(u.username.lower())

    if not user:
        guest_prog = get_user_progress(db, None)
        participants.append({
            "id": "guest",
            "username": "You",
            "coder_title": "Frontend Developer",
            "season_xp": getattr(guest_prog, "season_xp", 0) or 0,
            "streak": guest_prog.streak or 0,
            "avatar_config": models.DEFAULT_AVATAR_CONFIG.copy(),
            "is_current_user": True,
        })
        seen_usernames.add("you")

    participants.sort(key=lambda p: (p["season_xp"], p["streak"]), reverse=True)

    ranked_list = []
    user_rank = 1
    for index, p in enumerate(participants, start=1):
        assigned_title = p.get("coder_title") or "Frontend Developer"
        entry = {
            "rank": index,
            "id": p["id"],
            "username": p["username"],
            "coder_title": assigned_title,
            "season_xp": p["season_xp"],
            "streak": p["streak"],
            "avatar_config": p["avatar_config"],
            "is_current_user": p["is_current_user"],
        }
        ranked_list.append(entry)
        if p["is_current_user"]:
            user_rank = index

    return ranked_list, user_rank

def resolve_target_user(user_id: str, current_user: Optional[models.User], db: Session):
    import urllib.parse
    user_id = urllib.parse.unquote(urllib.parse.unquote(user_id)).strip()
    current_uid_str = f"user_{current_user.id}" if current_user else "guest"
    target_id_str = user_id
    if user_id.lower() in ("current", "me", "you"):
        target_id_str = current_uid_str

    target_user = None

    if target_id_str.startswith("user_"):
        try:
            uid_int = int(target_id_str.replace("user_", ""))
            target_user = db.query(models.User).filter(models.User.id == uid_int).first()
        except Exception:
            target_user = None
    elif target_id_str.isdigit():
        target_user = db.query(models.User).filter(models.User.id == int(target_id_str)).first()
        target_id_str = f"user_{target_id_str}"

    if not target_user and target_id_str != "guest":
        clean_name = target_id_str.lstrip("@").strip()
        target_user = db.query(models.User).filter(models.User.username.ilike(clean_name)).first()
        if not target_user and "_" in clean_name:
            target_user = db.query(models.User).filter(models.User.username.ilike(clean_name.replace("_", " "))).first()
        if target_user:
            target_id_str = f"user_{target_user.id}"

    return target_user, None, target_id_str

@app.get("/api/leaderboard")
@app.get("/leaderboard")
def get_leaderboard(
    user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    season = ensure_season(db)
    user_prog = get_user_progress(db, user)
    user_season_xp = getattr(user_prog, "season_xp", 0) or 0
    user_total_xp = user_prog.totalXP or 0
    user_streak = user_prog.streak or 0
    user_id_str = f"user_{user.id}" if user else "guest"
    user_name = user.username if user else "You"
    user_avatar = user.avatar_config if user else models.DEFAULT_AVATAR_CONFIG.copy()

    ranked_list, user_rank = compute_leaderboard_rankings(db, user)

    top_count = 20
    top_entries = ranked_list[:top_count]
    is_user_in_top = user_rank <= top_count

    now = datetime.utcnow()
    seconds_left = max(0, int((season.end_time - now).total_seconds()))

    return {
        "season": {
            "season_number": season.season_number,
            "title": season.title,
            "description": season.description,
            "end_time": season.end_time.isoformat(),
            "seconds_left": seconds_left,
            "rewards": {
                "first": season.reward_first or 200,
                "second": season.reward_second or 100,
                "third": season.reward_third or 50,
            }
        },
        "current_user": {
            "id": user_id_str,
            "username": user_name,
            "rank": user_rank,
            "season_xp": user_season_xp,
            "total_xp": user_total_xp,
            "streak": user_streak,
            "coder_title": (user.coder_title if (user and getattr(user, "coder_title", None)) else "Frontend Developer"),
            "avatar_config": user_avatar,
            "is_top": is_user_in_top,
        },
        "leaderboard": top_entries,
    }

@app.get("/api/users/{user_id}/profile")
@app.get("/users/{user_id}/profile")
def get_user_profile(
    user_id: str,
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    current_uid_str = f"user_{current_user.id}" if current_user else "guest"
    target_user, peer_data, target_id_str = resolve_target_user(user_id, current_user, db)

    ranked_list, user_rank = compute_leaderboard_rankings(db, current_user)
    target_ranked = next((r for r in ranked_list if r["id"] == target_id_str), None)
    if not target_ranked and target_user:
        target_ranked = next((r for r in ranked_list if r["username"].lower() == target_user.username.lower()), None)
    if not target_ranked and peer_data:
        target_ranked = next((r for r in ranked_list if r["username"].lower() == peer_data["username"].lower()), None)
    if not target_ranked and (target_id_str == "guest" or user_id.lower() in ("current", "me", "you")):
        target_ranked = next((r for r in ranked_list if r["is_current_user"]), None)

    current_rank = target_ranked["rank"] if target_ranked else user_rank
    season = ensure_season(db)
    season_days_left = max(0, int((season.end_time - datetime.utcnow()).total_seconds() // 86400))
    league = get_league_for_rank(current_rank)
    rank_title = None
    if current_rank == 1:
        rank_title = "Senior"
    elif current_rank == 2:
        rank_title = "Mid-Level"
    elif current_rank == 3:
        rank_title = "Junior"

    is_me = (current_uid_str == target_id_str)
    if current_user and target_user and current_user.id == target_user.id:
        is_me = True

    is_following = False
    if not is_me:
        follow_record = db.query(models.UserFollow).filter(
            models.UserFollow.follower_id == current_uid_str,
            models.UserFollow.following_id == target_id_str
        ).first()
        is_following = bool(follow_record)

    db_followers = db.query(models.UserFollow).filter(models.UserFollow.following_id == target_id_str).count()
    db_following = db.query(models.UserFollow).filter(models.UserFollow.follower_id == target_id_str).count()

    if target_user:
        target_uid_str = f"user_{target_user.id}"
        prog = db.query(models.UserProgress).filter(models.UserProgress.user_id == target_uid_str).first()
        joined_at = target_user.created_at.strftime("%Y-%m-%d") if target_user.created_at else None
        total_xp = (prog.totalXP or 0) if prog else 0
        season_xp = (getattr(prog, "season_xp", 0) or 0) if prog else 0
        avatar_config = target_user.avatar_config
        dev_stats = compute_user_dev_stats(db, target_uid_str, prog)

        return {
            "id": f"user_{target_user.id}",
            "username": target_user.username,
            "avatar_config": avatar_config,
            "joined_at": joined_at,
            "streak": dev_stats["streak"],
            "total_xp": total_xp,
            "season_xp": season_xp,
            "current_rank": current_rank,
            "rank_title": rank_title,
            "season_number": season.season_number or 1,
            "season_days_left": season_days_left,
            "league": league,
            "followers_count": db_followers,
            "following_count": db_following,
            "completed_nodes_count": dev_stats["completed_nodes_count"],
            "solved_mistakes_count": dev_stats["solved_mistakes_count"],
            "bugs_fixed": dev_stats["bugs_fixed"],
            "accuracy": dev_stats["accuracy"],
            "mastery_percent": dev_stats["mastery_percent"],
            "courses_mastery": dev_stats["courses_mastery"],
            "active_course": prog.active_course if prog and prog.active_course else "javascript",
            "is_following": is_following,
            "is_me": is_me,
            "coder_title": getattr(target_user, "coder_title", None) or (target_user.dev_card.get("specialtyTitle") if getattr(target_user, "dev_card", None) else None) or "Frontend Developer",
            "dev_card": getattr(target_user, "dev_card", {}) or {},
        }

    if target_id_str == "guest" or user_id.lower() in ("current", "me", "you"):
        guest_prog = get_user_progress(db, current_user)
        guest_uid = f"user_{current_user.id}" if current_user else "default_user"
        dev_stats = compute_user_dev_stats(db, guest_uid, guest_prog)
        effective_dev_card = (current_user.dev_card if current_user else (guest_prog.dev_card if guest_prog else {})) or {}

        return {
            "id": "current",
            "username": current_user.username if current_user else "You",
            "avatar_config": (current_user.avatar_config if current_user else models.DEFAULT_AVATAR_CONFIG.copy()),
            "joined_at": (current_user.created_at.strftime("%Y-%m-%d") if (current_user and current_user.created_at) else None),
            "streak": dev_stats["streak"],
            "total_xp": guest_prog.totalXP or 0,
            "season_xp": getattr(guest_prog, "season_xp", 0) or 0,
            "current_rank": current_rank,
            "rank_title": rank_title,
            "season_number": season.season_number or 1,
            "season_days_left": season_days_left,
            "league": league,
            "followers_count": db_followers,
            "following_count": db_following,
            "completed_nodes_count": dev_stats["completed_nodes_count"],
            "solved_mistakes_count": dev_stats["solved_mistakes_count"],
            "bugs_fixed": dev_stats["bugs_fixed"],
            "accuracy": dev_stats["accuracy"],
            "mastery_percent": dev_stats["mastery_percent"],
            "courses_mastery": dev_stats["courses_mastery"],
            "active_course": guest_prog.active_course if guest_prog and guest_prog.active_course else "javascript",
            "is_following": False,
            "is_me": True,
            "coder_title": (current_user.coder_title if (current_user and getattr(current_user, "coder_title", None)) else effective_dev_card.get("specialtyTitle") or "Frontend Developer"),
            "dev_card": effective_dev_card,
        }

    raise HTTPException(status_code=404, detail="User not found")

@app.get("/api/users/{user_id}/honors")
@app.get("/users/{user_id}/honors")
def get_user_honors(
    user_id: str,
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    target_user, peer_data, target_id_str = resolve_target_user(user_id, current_user, db)
    if not target_user and target_id_str != "guest":
        raise HTTPException(status_code=404, detail="User not found")

    if target_user:
        target_uid_str = f"user_{target_user.id}"
        prog = db.query(models.UserProgress).filter(models.UserProgress.user_id == target_uid_str).first()
        streak = (prog.streak or 0) if prog else 0
        total_xp = (prog.totalXP or 0) if prog else 0
        completed_nodes_count = len(prog.completed_nodes or []) if prog else 0
        solved_mistakes_count = db.query(models.UserMistake).filter(
            models.UserMistake.user_id == target_uid_str,
            or_(models.UserMistake.resolved == True, models.UserMistake.status == "resolved")
        ).count()
        db_followers = db.query(models.UserFollow).filter(models.UserFollow.following_id == target_uid_str).count()
    else:
        guest_prog = get_user_progress(db, current_user)
        guest_uid = f"user_{current_user.id}" if current_user else "default_user"
        streak = guest_prog.streak or 0
        total_xp = guest_prog.totalXP or 0
        completed_nodes_count = len(guest_prog.completed_nodes or []) if guest_prog else 0
        solved_mistakes_count = db.query(models.UserMistake).filter(
            models.UserMistake.user_id == guest_uid,
            or_(models.UserMistake.resolved == True, models.UserMistake.status == "resolved")
        ).count()
        db_followers = db.query(models.UserFollow).filter(models.UserFollow.following_id == guest_uid).count()

    ranked_list, user_rank = compute_leaderboard_rankings(db, current_user)
    target_ranked = next((r for r in ranked_list if r["id"] == target_id_str), None)
    current_rank = target_ranked["rank"] if target_ranked else user_rank

    return {
        "user_id": target_id_str,
        "streak": streak,
        "total_xp": total_xp,
        "completed_nodes_count": completed_nodes_count,
        "solved_mistakes_count": solved_mistakes_count,
        "followers_count": db_followers,
        "current_rank": current_rank,
    }

@app.post("/api/users/{user_id}/follow")
@app.post("/users/{user_id}/follow")
def follow_user(
    user_id: str,
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    current_uid_str = f"user_{current_user.id}" if current_user else "guest"
    target_user, peer_data, target_id_str = resolve_target_user(user_id, current_user, db)

    if not target_user and not peer_data and target_id_str != "guest":
        raise HTTPException(status_code=404, detail="User not found")

    if current_uid_str == target_id_str:
        raise HTTPException(status_code=400, detail="Cannot follow yourself")

    existing = db.query(models.UserFollow).filter(
        models.UserFollow.follower_id == current_uid_str,
        models.UserFollow.following_id == target_id_str
    ).first()

    if not existing:
        follow_record = models.UserFollow(follower_id=current_uid_str, following_id=target_id_str)
        db.add(follow_record)
        db.commit()

    db_followers = db.query(models.UserFollow).filter(models.UserFollow.following_id == target_id_str).count()

    return {
        "success": True,
        "is_following": True,
        "followers_count": db_followers
    }

@app.delete("/api/users/{user_id}/follow")
@app.delete("/users/{user_id}/follow")
def unfollow_user(
    user_id: str,
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    current_uid_str = f"user_{current_user.id}" if current_user else "guest"
    target_user, peer_data, target_id_str = resolve_target_user(user_id, current_user, db)

    if not target_user and not peer_data and target_id_str != "guest":
        raise HTTPException(status_code=404, detail="User not found")

    db.query(models.UserFollow).filter(
        models.UserFollow.follower_id == current_uid_str,
        models.UserFollow.following_id == target_id_str
    ).delete()
    db.commit()

    db_followers = db.query(models.UserFollow).filter(models.UserFollow.following_id == target_id_str).count()

    return {
        "success": True,
        "is_following": False,
        "followers_count": db_followers
    }

@app.get("/api/users/{user_id}/connections")
@app.get("/users/{user_id}/connections")
def get_user_connections(
    user_id: str,
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    target_user, peer_data, target_id_str = resolve_target_user(user_id, current_user, db)
    if not target_user and not peer_data and target_id_str != "guest":
        raise HTTPException(status_code=404, detail="User not found")

    ranked_list, _ = compute_leaderboard_rankings(db, current_user)
    ranked_map = {r["id"]: r for r in ranked_list}

    def format_conn_user(uid: str):
        if not uid.startswith("user_"):
            return None
        try:
            u_id_int = int(uid.replace("user_", ""))
        except Exception:
            return None
        u = db.query(models.User).filter(models.User.id == u_id_int).first()
        if not u:
            return None
        prog = db.query(models.UserProgress).filter(models.UserProgress.user_id == uid).first()
        ranked = ranked_map.get(uid)
        rank = ranked["rank"] if ranked else None
        return {
            "id": uid,
            "username": u.username,
            "avatar_config": u.avatar_config or models.DEFAULT_AVATAR_CONFIG.copy(),
            "total_xp": (prog.totalXP or 0) if prog else 0,
            "season_xp": (getattr(prog, "season_xp", 0) or 0) if prog else 0,
            "rank": rank,
            "rank_title": "Senior" if rank == 1 else "Mid-Level" if rank == 2 else "Junior" if rank == 3 else None
        }

    db_follower_records = db.query(models.UserFollow).filter(models.UserFollow.following_id == target_id_str).all()
    followers = []
    seen_follower_ids = set()
    for rec in db_follower_records:
        user_obj = format_conn_user(rec.follower_id)
        if user_obj and user_obj["id"] not in seen_follower_ids:
            followers.append(user_obj)
            seen_follower_ids.add(user_obj["id"])

    db_following_records = db.query(models.UserFollow).filter(models.UserFollow.follower_id == target_id_str).all()
    following = []
    seen_following_ids = set()
    for rec in db_following_records:
        user_obj = format_conn_user(rec.following_id)
        if user_obj and user_obj["id"] not in seen_following_ids:
            following.append(user_obj)
            seen_following_ids.add(user_obj["id"])

    return {
        "followers": followers,
        "following": following
    }


class CreateSquadRequest(BaseModel):
    project_title: str
    project_vibe: Optional[str] = "Portfolio Builder"
    my_role: str = "Frontend"
    needed_role: str = "Backend"
    min_streak: int = 0
    contact_link: Optional[str] = ""
    description: str = ""

class CreateShowcaseProjectRequest(BaseModel):
    title: str
    description: str = ""
    url: str = ""
    tech_tags: Optional[List[str]] = None

def infer_role_category(title: str) -> str:
    low = (title or "").lower()
    if "front" in low or "ui" in low or "react" in low:
        return "Frontend"
    if "back" in low or "api" in low or "system" in low or "data" in low or "python" in low:
        return "Backend"
    return "Full-Stack"

def sync_user_projects_to_showcase(db: Session, creator_id: str, creator_username: str, dev_card: dict):
    projects = dev_card.get("projects") if isinstance(dev_card, dict) else None
    if not isinstance(projects, list):
        return
    tech_stack = dev_card.get("techStack") if isinstance(dev_card.get("techStack"), list) else ["JavaScript", "React"]
    for p in projects[:2]:
        if not isinstance(p, dict):
            continue
        p_title = str(p.get("title") or "").strip()
        if not p_title or p_title.lower() == "untitled project":
            continue
        existing = db.query(models.ShowcaseProject).filter(
            models.ShowcaseProject.creator_id == creator_id,
            models.ShowcaseProject.title.ilike(p_title)
        ).first()
        if existing:
            existing.description = str(p.get("desc") or existing.description or "").strip()
            existing.url = str(p.get("link") or existing.url or "").strip()
            existing.tech_tags = tech_stack[:4]
        else:
            item = models.ShowcaseProject(
                creator_id=creator_id,
                creator_username=creator_username,
                title=p_title,
                description=str(p.get("desc") or "Built on Buggy.").strip(),
                url=str(p.get("link") or "https://github.com").strip(),
                boost_count=3,
            )
            item.tech_tags = tech_stack[:4]
            db.add(item)
    db.commit()

def ensure_dev_hub_seed(db: Session):
    curated_profiles = {
        "ILIYASFS": {
            "specialtyTitle": "Full-Stack Developer",
            "collabStatus": "Open to Work",
            "bio": "Building interactive web apps with React & FastAPI.",
            "techStack": ["JavaScript", "React", "TypeScript", "Python", "FastAPI"],
            "is_super": True,
            "githubUrl": "https://github.com/iliyasfs2",
            "linkedinUrl": "https://linkedin.com",
            "websiteUrl": "https://buggy.dev",
            "telegramUrl": "https://t.me",
            "email": "coder@buggy.dev",
            "projects": [
                {
                    "id": "p1",
                    "title": "Buggy Platform",
                    "desc": "Gamified platform to learn coding and debugging.",
                    "link": "https://buggy.dev"
                },
                {
                    "id": "p2",
                    "title": "SmartCode Runner",
                    "desc": "Interactive client sandbox with AST diagnostics.",
                    "link": "https://github.com"
                }
            ]
        },
        "Alex": {
            "specialtyTitle": "Backend Developer",
            "collabStatus": "Looking for Teammate",
            "bio": "Distributed systems & high-speed Python APIs.",
            "techStack": ["Python", "FastAPI", "SQL", "Git", "TypeScript"],
            "is_super": True,
            "githubUrl": "https://github.com",
            "telegramUrl": "https://t.me",
            "projects": [
                {
                    "id": "alex_p1",
                    "title": "PulseQueue Engine",
                    "desc": "Async task runner with zero-latency worker pools.",
                    "link": "https://github.com"
                }
            ]
        },
        "testcoder": {
            "specialtyTitle": "Frontend Developer",
            "collabStatus": "Open to Work",
            "bio": "Crafting pixel-perfect React & Next.js interfaces.",
            "techStack": ["JavaScript", "TypeScript", "React", "Next.js", "Tailwind CSS"],
            "is_super": False,
            "githubUrl": "https://github.com",
            "projects": [
                {
                    "id": "tc_p1",
                    "title": "PixelFlow UI Kit",
                    "desc": "Tactile 3D component library for gamified web apps.",
                    "link": "https://github.com"
                }
            ]
        },
        "avatar_tester": {
            "specialtyTitle": "Full-Stack Developer",
            "collabStatus": "Learning & Coding",
            "bio": "Mastering JavaScript algorithms and clean architecture.",
            "techStack": ["JavaScript", "Node.js", "React", "Git"],
            "is_super": False,
            "githubUrl": "https://github.com",
            "projects": [
                {
                    "id": "av_p1",
                    "title": "CodeTrace Visualizer",
                    "desc": "Step-by-step call stack visualizer for learners.",
                    "link": "https://github.com"
                }
            ]
        }
    }

    changed = False
    for uname, card_template in curated_profiles.items():
        u = db.query(models.User).filter(models.User.username.ilike(uname)).first()
        if u:
            existing_card = dict(u.dev_card or {})
            if not existing_card:
                u.dev_card = card_template
                u.coder_title = card_template["specialtyTitle"]
                changed = True
            elif uname == "ILIYASFS" and not existing_card.get("is_super"):
                existing_card["is_super"] = True
                u.dev_card = existing_card
                changed = True

    if changed:
        db.commit()

    if db.query(models.SquadRequest).count() == 0:
        initial_squads = [
            models.SquadRequest(
                creator_id="user_2",
                creator_username="ILIYASFS",
                project_title="Buggy AI Pair Debugger",
                my_role="Full-Stack",
                needed_role="Frontend",
                min_streak=3,
                description="Building a live multiplayer debug arena. Need a React/Tailwind wizard for animations!"
            ),
            models.SquadRequest(
                creator_id="user_6",
                creator_username="Alex",
                project_title="Realtime Code Collab CLI",
                my_role="Backend",
                needed_role="Frontend",
                min_streak=5,
                description="FastAPI WebSocket backend is ready. Looking for a frontend teammate to build the dashboard."
            ),
            models.SquadRequest(
                creator_id="user_1",
                creator_username="testcoder",
                project_title="OpenSource Algorithm Cards",
                my_role="Frontend",
                needed_role="Backend",
                min_streak=2,
                description="Interactive flashcards for JS & Python interviews. Need help with API & SQLite sync."
            ),
        ]
        for sq in initial_squads:
            db.add(sq)
        db.commit()

    if db.query(models.ShowcaseProject).count() == 0:
        initial_showcase = [
            models.ShowcaseProject(
                creator_id="user_2",
                creator_username="ILIYASFS",
                title="Buggy Platform",
                description="Gamified platform to learn coding, hunt bugs, and build your verified developer identity.",
                url="https://buggy.dev",
                boost_count=48,
            ),
            models.ShowcaseProject(
                creator_id="user_6",
                creator_username="Alex",
                title="PulseQueue Engine",
                description="High-speed async job queue in Python & FastAPI with live telemetry.",
                url="https://github.com",
                boost_count=31,
            ),
            models.ShowcaseProject(
                creator_id="user_2",
                creator_username="ILIYASFS",
                title="SmartCode Runner",
                description="Interactive client sandbox with real-time AST diagnostics and zero-latency execution.",
                url="https://github.com",
                boost_count=24,
            ),
            models.ShowcaseProject(
                creator_id="user_1",
                creator_username="testcoder",
                title="PixelFlow UI Kit",
                description="3D tactile dark-navy UI components built with Tailwind CSS and Framer Motion.",
                url="https://github.com",
                boost_count=17,
            ),
        ]
        initial_showcase[0].tech_tags = ["JavaScript", "React", "TypeScript", "FastAPI"]
        initial_showcase[1].tech_tags = ["Python", "FastAPI", "SQL"]
        initial_showcase[2].tech_tags = ["TypeScript", "JavaScript", "React"]
        initial_showcase[3].tech_tags = ["React", "Next.js", "Tailwind CSS"]
        for sh in initial_showcase:
            db.add(sh)
        db.commit()

def build_talent_entry(db: Session, u: models.User, current_uid_str: str) -> dict:
    uid_str = f"user_{u.id}"
    prog = db.query(models.UserProgress).filter(models.UserProgress.user_id == uid_str).first()
    dev_stats = compute_user_dev_stats(db, uid_str, prog)
    card = dict(u.dev_card or {})
    specialty = card.get("specialtyTitle") or getattr(u, "coder_title", None) or "Full-Stack Developer"
    status_val = card.get("collabStatus") or "Open to Work"
    bio_val = card.get("bio") or "Building interactive web apps and solving bugs on Buggy."
    tech_stack = card.get("techStack") if isinstance(card.get("techStack"), list) and len(card.get("techStack")) > 0 else ["JavaScript", "React", "Python"]
    projects = card.get("projects") if isinstance(card.get("projects"), list) else []
    total_xp = int(prog.totalXP or 0) if prog else 0
    is_super = bool(card.get("is_super") or total_xp >= 300)

    is_following = False
    if current_uid_str != uid_str:
        is_following = bool(
            db.query(models.UserFollow).filter(
                models.UserFollow.follower_id == current_uid_str,
                models.UserFollow.following_id == uid_str
            ).first()
        )
    followers_count = db.query(models.UserFollow).filter(models.UserFollow.following_id == uid_str).count()

    return {
        "id": uid_str,
        "username": u.username,
        "display_name": u.username,
        "avatar_config": u.avatar_config or models.DEFAULT_AVATAR_CONFIG.copy(),
        "specialty_title": specialty,
        "role_category": infer_role_category(specialty),
        "collab_status": status_val,
        "bio": bio_val,
        "tech_stack": tech_stack,
        "projects": projects,
        "social_links": {
            "githubUrl": card.get("githubUrl") or "",
            "linkedinUrl": card.get("linkedinUrl") or "",
            "websiteUrl": card.get("websiteUrl") or "",
            "telegramUrl": card.get("telegramUrl") or "",
            "email": card.get("email") or "",
        },
        "is_super": is_super,
        "streak": dev_stats["streak"],
        "bugs_fixed": dev_stats["bugs_fixed"],
        "accuracy": dev_stats["accuracy"],
        "total_xp": total_xp,
        "mastery_percent": dev_stats["mastery_percent"],
        "courses_mastery": dev_stats["courses_mastery"],
        "is_following": is_following,
        "followers_count": followers_count,
        "is_me": current_uid_str == uid_str,
    }

@app.get("/api/dev-hub/talents")
def get_dev_hub_talents(
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    ensure_dev_hub_seed(db)
    current_uid_str = f"user_{current_user.id}" if current_user else "guest"
    users = db.query(models.User).all()
    talents = [build_talent_entry(db, u, current_uid_str) for u in users if not u.username.startswith("store_user_") and not u.username.startswith("new_179")]
    talents.sort(key=lambda t: (1 if t["is_super"] else 0, t["streak"], t["bugs_fixed"], t["total_xp"]), reverse=True)
    return {"talents": talents}

@app.get("/api/dev-hub/squads")
def get_dev_hub_squads(
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    ensure_dev_hub_seed(db)
    current_uid_str = f"user_{current_user.id}" if current_user else "default_user"
    rows = db.query(models.SquadRequest).order_by(models.SquadRequest.id.desc()).all()
    user_map = {f"user_{u.id}": u for u in db.query(models.User).all()}

    items = []
    for sq in rows:
        u = user_map.get(sq.creator_id)
        if u:
            talent_meta = build_talent_entry(db, u, current_uid_str)
        else:
            guest_prog = get_user_progress(db, None)
            dev_stats = compute_user_dev_stats(db, "default_user", guest_prog)
            guest_card = dict(guest_prog.dev_card or {})
            talent_meta = {
                "id": sq.creator_id,
                "username": sq.creator_username,
                "display_name": sq.creator_username,
                "avatar_config": models.DEFAULT_AVATAR_CONFIG.copy(),
                "specialty_title": guest_card.get("specialtyTitle") or "Full-Stack Developer",
                "collab_status": guest_card.get("collabStatus") or "Looking for Teammate",
                "bio": guest_card.get("bio") or "Building cool projects on Buggy.",
                "tech_stack": guest_card.get("techStack") or ["JavaScript", "React"],
                "projects": guest_card.get("projects") or [],
                "social_links": {
                    "githubUrl": guest_card.get("githubUrl") or "",
                    "linkedinUrl": guest_card.get("linkedinUrl") or "",
                    "websiteUrl": guest_card.get("websiteUrl") or "",
                    "telegramUrl": guest_card.get("telegramUrl") or "",
                    "email": guest_card.get("email") or "",
                },
                "is_super": False,
                "streak": dev_stats["streak"],
                "bugs_fixed": dev_stats["bugs_fixed"],
                "accuracy": dev_stats["accuracy"],
                "mastery_percent": dev_stats["mastery_percent"],
                "courses_mastery": dev_stats["courses_mastery"],
            }
        items.append({
            "id": sq.id,
            "creator_id": sq.creator_id,
            "creator_username": talent_meta["username"],
            "project_title": sq.project_title,
            "project_vibe": getattr(sq, "project_vibe", None) or "Portfolio Builder",
            "my_role": sq.my_role,
            "needed_role": sq.needed_role,
            "min_streak": sq.min_streak or 0,
            "contact_link": getattr(sq, "contact_link", None) or talent_meta["social_links"].get("telegramUrl") or talent_meta["social_links"].get("linkedinUrl") or "",
            "description": sq.description or "",
            "created_at": sq.created_at.isoformat() if sq.created_at else None,
            "is_mine": sq.creator_id == current_uid_str,
            "creator": talent_meta,
        })
    return {"squads": items}

@app.post("/api/dev-hub/squads")
def create_dev_hub_squad(
    req: CreateSquadRequest,
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    clean_title = (req.project_title or "").strip()
    if len(clean_title) < 2:
        raise HTTPException(status_code=400, detail="Project title is required")
    creator_id = f"user_{current_user.id}" if current_user else "default_user"
    creator_username = current_user.username if current_user else "Developer"

    sq = models.SquadRequest(
        creator_id=creator_id,
        creator_username=creator_username,
        project_title=clean_title[:60],
        project_vibe=(req.project_vibe or "Portfolio Builder").strip()[:40],
        my_role=(req.my_role or "Frontend").strip()[:30],
        needed_role=(req.needed_role or "Backend").strip()[:30],
        min_streak=max(0, min(365, int(req.min_streak or 0))),
        contact_link=(req.contact_link or "").strip()[:160],
        description=(req.description or "").strip()[:240],
    )
    db.add(sq)
    db.commit()
    db.refresh(sq)
    return get_dev_hub_squads(current_user=current_user, db=db)

@app.delete("/api/dev-hub/squads/{squad_id}")
def delete_dev_hub_squad(
    squad_id: int,
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    creator_id = f"user_{current_user.id}" if current_user else "default_user"
    sq = db.query(models.SquadRequest).filter(models.SquadRequest.id == squad_id).first()
    if not sq:
        raise HTTPException(status_code=404, detail="Squad request not found")
    if sq.creator_id != creator_id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this squad request")
    db.delete(sq)
    db.commit()
    return {"success": True, "deleted_id": squad_id}

@app.get("/api/dev-hub/showcase")
def get_dev_hub_showcase(
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    ensure_dev_hub_seed(db)
    for u in db.query(models.User).all():
        if u.dev_card:
            sync_user_projects_to_showcase(db, f"user_{u.id}", u.username, u.dev_card)
    guest_prog = db.query(models.UserProgress).filter(models.UserProgress.user_id == "default_user").first()
    if guest_prog and guest_prog.dev_card:
        sync_user_projects_to_showcase(db, "default_user", "Developer", guest_prog.dev_card)
    voter_id = f"user_{current_user.id}" if current_user else "default_user"
    rows = db.query(models.ShowcaseProject).order_by(
        models.ShowcaseProject.boost_count.desc(),
        models.ShowcaseProject.id.asc()
    ).all()
    voted_ids = {
        v.project_id
        for v in db.query(models.ShowcaseVote).filter(models.ShowcaseVote.voter_id == voter_id).all()
    }
    user_map = {f"user_{u.id}": u for u in db.query(models.User).all()}

    projects = []
    for idx, proj in enumerate(rows, start=1):
        u = user_map.get(proj.creator_id)
        if u:
            creator_meta = build_talent_entry(db, u, voter_id)
        else:
            guest_prog = get_user_progress(db, None)
            dev_stats = compute_user_dev_stats(db, "default_user", guest_prog)
            guest_card = dict(guest_prog.dev_card or {})
            creator_meta = {
                "id": proj.creator_id,
                "username": proj.creator_username,
                "display_name": proj.creator_username,
                "avatar_config": models.DEFAULT_AVATAR_CONFIG.copy(),
                "specialty_title": guest_card.get("specialtyTitle") or "Full-Stack Developer",
                "collab_status": guest_card.get("collabStatus") or "Open to Work",
                "bio": guest_card.get("bio") or "Building interactive web apps.",
                "tech_stack": guest_card.get("techStack") or ["JavaScript", "React"],
                "projects": guest_card.get("projects") or [],
                "social_links": {
                    "githubUrl": guest_card.get("githubUrl") or "",
                    "linkedinUrl": guest_card.get("linkedinUrl") or "",
                    "websiteUrl": guest_card.get("websiteUrl") or "",
                    "telegramUrl": guest_card.get("telegramUrl") or "",
                    "email": guest_card.get("email") or "",
                },
                "is_super": False,
                "streak": dev_stats["streak"],
                "bugs_fixed": dev_stats["bugs_fixed"],
                "accuracy": dev_stats["accuracy"],
                "mastery_percent": dev_stats["mastery_percent"],
                "courses_mastery": dev_stats["courses_mastery"],
            }
        projects.append({
            "id": proj.id,
            "rank": idx,
            "creator_id": proj.creator_id,
            "creator_username": creator_meta["username"],
            "title": proj.title,
            "description": proj.description or "",
            "url": proj.url or "https://github.com",
            "tech_tags": proj.tech_tags or ["JavaScript", "React"],
            "boost_count": proj.boost_count or 0,
            "has_boosted": proj.id in voted_ids,
            "creator": creator_meta,
        })
    return {"projects": projects}

@app.post("/api/dev-hub/showcase")
def create_dev_hub_showcase_project(
    req: CreateShowcaseProjectRequest,
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    clean_title = (req.title or "").strip()
    if len(clean_title) < 2:
        raise HTTPException(status_code=400, detail="Project title is required")
    creator_id = f"user_{current_user.id}" if current_user else "default_user"
    creator_username = current_user.username if current_user else "Developer"

    item = models.ShowcaseProject(
        creator_id=creator_id,
        creator_username=creator_username,
        title=clean_title[:60],
        description=(req.description or "").strip()[:240],
        url=(req.url or "https://github.com").strip(),
        boost_count=1,
    )
    item.tech_tags = (req.tech_tags or ["JavaScript", "React"])[:5]
    db.add(item)
    db.commit()
    db.refresh(item)
    vote = models.ShowcaseVote(project_id=item.id, voter_id=creator_id)
    db.add(vote)
    db.commit()
    return get_dev_hub_showcase(current_user=current_user, db=db)

@app.post("/api/dev-hub/showcase/{project_id}/boost")
def toggle_boost_showcase_project(
    project_id: int,
    current_user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    voter_id = f"user_{current_user.id}" if current_user else "default_user"
    proj = db.query(models.ShowcaseProject).filter(models.ShowcaseProject.id == project_id).first()
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    existing_vote = db.query(models.ShowcaseVote).filter(
        models.ShowcaseVote.project_id == project_id,
        models.ShowcaseVote.voter_id == voter_id
    ).first()

    if existing_vote:
        db.delete(existing_vote)
        proj.boost_count = max(0, (proj.boost_count or 1) - 1)
        has_boosted = False
    else:
        new_vote = models.ShowcaseVote(project_id=project_id, voter_id=voter_id)
        db.add(new_vote)
        proj.boost_count = (proj.boost_count or 0) + 1
        has_boosted = True

    db.commit()
    db.refresh(proj)
    return {
        "success": True,
        "project_id": proj.id,
        "boost_count": proj.boost_count,
        "has_boosted": has_boosted,
    }

@app.post("/api/dev/simulate-season-end")
@app.post("/dev/simulate-season-end")
def simulate_season_end(
    user: Optional[models.User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    season = ensure_season(db)
    reward_and_reset_season(db, season)
    user_prog = get_user_progress(db, user)
    return {
        "success": True,
        "season": {
            "season_number": season.season_number,
            "title": season.title,
            "end_time": season.end_time.isoformat(),
            "seconds_left": max(0, int((season.end_time - datetime.utcnow()).total_seconds())),
        },
        "user_progress": get_progress_response(user_prog, db)
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
