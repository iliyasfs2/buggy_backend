with open("main.py", "r") as f:
    content = f.read()
content = content.replace("""    # --- Daily Quests Logic ---
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
    
    db.commit()""", "db.commit()")
content = content.replace("""    # Save arrays back via setter
    progress.completed_nodes = completed
    progress.unlocked_nodes = unlocked

    db.commit()""", """    # Save arrays back via setter
    progress.completed_nodes = completed
    progress.unlocked_nodes = unlocked
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

    db.commit()""")

with open("main.py", "w") as f:
    f.write(content)
