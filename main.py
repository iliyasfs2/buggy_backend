import json
import os
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Buggy Progress API")

# Setup CORS to allow Next.js frontend to communicate
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_FILE = "db.json"

DEFAULT_STATE = {
    "totalXP": 1250,
    "streak": 14,
    "battery": 5,
    "completed_nodes": [],
    "unlocked_nodes": ["js-1-1"]
}

def read_db() -> dict:
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                pass
    return DEFAULT_STATE.copy()

def write_db(state: dict):
    with open(DB_FILE, "w") as f:
        json.dump(state, f, indent=2)

class CompleteNodeRequest(BaseModel):
    nodeId: str
    earnedXp: int
    nextNodeId: Optional[str] = None

@app.get("/progress")
def get_progress():
    return read_db()

@app.post("/progress/complete-node")
def complete_node(req: CompleteNodeRequest):
    state = read_db()

    # Add XP
    if req.earnedXp:
        state["totalXP"] += req.earnedXp

    # Update Streak logic
    if req.nodeId and req.nodeId not in state["completed_nodes"]:
        state["streak"] += 1

    # Mark node as completed
    if req.nodeId and req.nodeId not in state["completed_nodes"]:
        state["completed_nodes"].append(req.nodeId)

    # Unlock next node
    if req.nextNodeId and req.nextNodeId not in state["unlocked_nodes"]:
        state["unlocked_nodes"].append(req.nextNodeId)

    write_db(state)
    return state

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
