import re
from datetime import date
import json

with open("main.py", "r") as f:
    content = f.read()
content = content.replace(
    "nextNodeId: Optional[str] = None",
    "nextNodeId: Optional[str] = None\n    flawless: bool = False"
)
quest_logic = """
    from datetime import date
    import json
    
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
    
    db.commit()"""

content = content.replace("db.commit()", quest_logic, 1)
def fix_return(c):
    c = re.sub(r'"bytes": progress.bytes,\n\s*"active_course": progress.active_course,', '', c)
    c = c.replace(
        '"unlocked_nodes": progress.unlocked_nodes',
        '"unlocked_nodes": progress.unlocked_nodes,\n        "daily_quests_date": progress.daily_quests_date,\n        "daily_quests_state": progress.daily_quests_state'
    )
    return c

content = fix_return(content)

claim_endpoint = """
class ClaimQuestRequest(BaseModel):
    questId: str

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
    
    return {
        "bytes": progress.bytes,
        "daily_quests_state": progress.daily_quests_state
    }

if __name__ == "__main__":"""

content = content.replace('if __name__ == "__main__":', claim_endpoint)

with open("main.py", "w") as f:
    f.write(content)
