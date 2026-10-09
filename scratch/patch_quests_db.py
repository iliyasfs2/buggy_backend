from sqlalchemy import create_engine, text
from datetime import date
import json

DEFAULT_QUESTS = json.dumps([
  {"id": "q1", "title": "Complete 1 lesson", "current": 0, "target": 1, "reward_bytes": 10, "claimed": False},
  {"id": "q2", "title": "Earn 30 XP", "current": 0, "target": 30, "reward_bytes": 15, "claimed": False},
  {"id": "q3", "title": "One flawless lesson", "current": 0, "target": 1, "reward_bytes": 25, "claimed": False}
])

engine = create_engine("sqlite:///./roadmap.db")
with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE user_progress ADD COLUMN daily_quests_date VARCHAR DEFAULT ''"))
        conn.commit()
        print("Added daily_quests_date")
    except Exception as e:
        print("Error/Exists daily_quests_date:", e)
        
    try:
        conn.execute(text(f"ALTER TABLE user_progress ADD COLUMN daily_quests_state TEXT DEFAULT '{DEFAULT_QUESTS}'"))
        conn.commit()
        print("Added daily_quests_state")
    except Exception as e:
        print("Error/Exists daily_quests_state:", e)
