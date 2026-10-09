import re

with open("main.py", "r") as f:
    content = f.read()
content = content.replace(
    '"battery": progress.battery,',
    '"battery": progress.battery,\n        "bytes": progress.bytes,'
)
model_str = """class CompleteNodeRequest(BaseModel):
    nodeId: str
    earnedXp: int
    nextNodeId: Optional[str] = None

class ByteTransactionRequest(BaseModel):
    amount: int
"""
content = content.replace(
    """class CompleteNodeRequest(BaseModel):
    nodeId: str
    earnedXp: int
    nextNodeId: Optional[str] = None""",
    model_str
)
endpoint_str = """@app.post("/bytes/transaction")
def bytes_transaction(req: ByteTransactionRequest, db: Session = Depends(get_db)):
    progress = get_user_progress(db)
    progress.bytes += req.amount
    db.commit()
    db.refresh(progress)
    return {"bytes": progress.bytes}

if __name__ == "__main__":"""
content = content.replace('if __name__ == "__main__":', endpoint_str)

with open("main.py", "w") as f:
    f.write(content)
