from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import UserProgress

engine = create_engine("sqlite:///./roadmap.db")
Session = sessionmaker(bind=engine)
session = Session()

user = session.query(UserProgress).filter_by(user_id="default_user").first()
if user:
    user.totalXP = 0
    user.streak = 0
    user.battery = 5
    user._completed_nodes = "[]"
    user._unlocked_nodes = '["js-1-1"]'
    session.commit()
    print("Database reset successfully.")
else:
    print("User not found.")
session.close()
