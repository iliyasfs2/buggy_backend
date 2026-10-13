from sqlalchemy import Column, Integer, String, Text, DateTime
from database import Base
import json
from datetime import datetime

DEFAULT_AVATAR_CONFIG = {
    "skin_color": "#FCD34D",
    "hair_style": "short_wavy",
    "hair_color": "#1E293B",
    "eye_style": "round",
    "expression": "happy",
    "accessory": "yellow_glasses",
    "shirt_color": "#0284C7",
    "bg_color": "#0F172A"
}

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    age = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    _avatar = Column("avatar", Text, default='{}')
    _avatar_config = Column("avatar_config", Text, default=json.dumps(DEFAULT_AVATAR_CONFIG))

    @property
    def avatar(self):
        try:
            return json.loads(self._avatar) if self._avatar else {}
        except Exception:
            return {}

    @avatar.setter
    def avatar(self, value):
        self._avatar = json.dumps(value)

    @property
    def avatar_config(self):
        try:
            val = json.loads(self._avatar_config) if self._avatar_config else {}
            if not val or not isinstance(val, dict):
                return DEFAULT_AVATAR_CONFIG.copy()
            res = DEFAULT_AVATAR_CONFIG.copy()
            res.update(val)
            return res
        except Exception:
            return DEFAULT_AVATAR_CONFIG.copy()

    @avatar_config.setter
    def avatar_config(self, value):
        self._avatar_config = json.dumps(value)

class UserProgress(Base):
    __tablename__ = "user_progress"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, unique=True, index=True, default="default_user")
    totalXP = Column(Integer, default=0)
    streak = Column(Integer, default=0)
    battery = Column(Integer, default=5)
    bytes = Column(Integer, default=100)
    active_course = Column(String, default="javascript")
    daily_quests_date = Column(String, default="")
    _daily_quests_state = Column("daily_quests_state", Text, default='[]')
    _completed_nodes = Column("completed_nodes", Text, default="[]")
    _unlocked_nodes = Column("unlocked_nodes", Text, default='["js-1-1"]')

    @property
    def completed_nodes(self):
        return json.loads(self._completed_nodes)

    @completed_nodes.setter
    def completed_nodes(self, value):
        self._completed_nodes = json.dumps(value)

    @property
    def unlocked_nodes(self):
        return json.loads(self._unlocked_nodes)

    @unlocked_nodes.setter
    def unlocked_nodes(self, value):
        self._unlocked_nodes = json.dumps(value)

    @property
    def daily_quests_state(self):
        return json.loads(self._daily_quests_state)

    @daily_quests_state.setter
    def daily_quests_state(self, value):
        self._daily_quests_state = json.dumps(value)
