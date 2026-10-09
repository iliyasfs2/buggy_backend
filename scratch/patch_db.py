from sqlalchemy import create_engine, text

engine = create_engine("sqlite:///./roadmap.db")
with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE user_progress ADD COLUMN bytes INTEGER DEFAULT 100"))
        conn.commit()
        print("Column 'bytes' added.")
    except Exception as e:
        print("Column might already exist or error:", e)
