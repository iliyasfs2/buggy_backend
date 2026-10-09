import re

with open("main.py", "r") as f:
    content = f.read()
content = content.replace(
    '"bytes": progress.bytes,',
    '"bytes": progress.bytes,\n        "active_course": progress.active_course,'
)
content = content.replace(
    '"battery": progress.battery,',
    '"battery": progress.battery,\n        "bytes": progress.bytes,\n        "active_course": progress.active_course,'
)
model_str = """class SwitchCourseRequest(BaseModel):
    courseId: str

class ByteTransactionRequest"""
content = content.replace("class ByteTransactionRequest", model_str)
endpoint_str = """@app.post("/user/course")
def switch_course(req: SwitchCourseRequest, db: Session = Depends(get_db)):
    progress = get_user_progress(db)
    progress.active_course = req.courseId
    db.commit()
    db.refresh(progress)
    return {
        "totalXP": progress.totalXP,
        "streak": progress.streak,
        "battery": progress.battery,
        "bytes": progress.bytes,
        "active_course": progress.active_course,
        "completed_nodes": progress.completed_nodes,
        "unlocked_nodes": progress.unlocked_nodes
    }

if __name__ == "__main__":"""
content = content.replace('if __name__ == "__main__":', endpoint_str)

with open("main.py", "w") as f:
    f.write(content)
