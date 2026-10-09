from sqlalchemy import Column, Integer, String, Text
from database import Base
import json

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
