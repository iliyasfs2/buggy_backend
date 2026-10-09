from sqlalchemy import create_engine, text

engine = create_engine("sqlite:///./roadmap.db")
with engine.connect() as conn:
    try:
        conn.execute(text("ALTER TABLE user_progress ADD COLUMN active_course VARCHAR DEFAULT 'javascript'"))
        conn.commit()
        print("Column 'active_course' added.")
    except Exception as e:
        print("Error or already exists:", e)
