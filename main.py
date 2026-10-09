import json
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import date

import models
from database import engine, get_db
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Buggy Progress API (SQLAlchemy)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
def get_user_progress(db: Session, user_id: str = "default_user"):
    progress = db.query(models.UserProgress).filter(models.UserProgress.user_id == user_id).first()
    if not progress:
        progress = models.UserProgress(user_id=user_id)
        db.add(progress)
        db.commit()
        db.refresh(progress)
    return progress

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

@app.get("/progress")
def get_progress(db: Session = Depends(get_db)):
    progress = get_user_progress(db)
    return get_progress_response(progress)

@app.post("/progress/complete-node")
def complete_node(req: CompleteNodeRequest, db: Session = Depends(get_db)):
    progress = get_user_progress(db)
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
    today_str = date.today().isoformat()
    quests = progress.daily_quests_state
    if progress.daily_quests_date != today_str or not quests:
        progress.daily_quests_date = today_str
        quests = [
            {"id": "q1", "title": "Complete 1 lesson", "current": 0, "target": 1, "reward_bytes": 10, "claimed": False},
            {"id": "q2", "title": "Earn 30 XP", "current": 0, "target": 30, "reward_bytes": 15, "claimed": False},
            {"id": "q3", "title": "One flawless lesson", "current": 0, "target": 1, "reward_bytes": 25, "claimed": False}
        ]
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
def decrease_battery(db: Session = Depends(get_db)):
    progress = get_user_progress(db)
    if progress.battery > 0:
        progress.battery -= 1
        db.commit()
        db.refresh(progress)
    return {"battery": progress.battery}

@app.post("/progress/recharge-battery")
def recharge_battery(db: Session = Depends(get_db)):
    progress = get_user_progress(db)
    if progress.battery < 5:
        progress.battery += 1
        db.commit()
        db.refresh(progress)
    return {"battery": progress.battery}

@app.post("/bytes/transaction")
def bytes_transaction(req: ByteTransactionRequest, db: Session = Depends(get_db)):
    progress = get_user_progress(db)
    progress.bytes += req.amount
    db.commit()
    db.refresh(progress)
    return {"bytes": progress.bytes}

@app.post("/user/course")
def switch_course(req: SwitchCourseRequest, db: Session = Depends(get_db)):
    progress = get_user_progress(db)
    progress.active_course = req.courseId
    db.commit()
    db.refresh(progress)
    return get_progress_response(progress)

@app.post("/quests/claim")
def claim_quest(req: ClaimQuestRequest, db: Session = Depends(get_db)):
    progress = get_user_progress(db)
    quests = progress.daily_quests_state
    
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
