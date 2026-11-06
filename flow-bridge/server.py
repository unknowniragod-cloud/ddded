import os, sqlite3, uuid
from datetime import datetime, timezone
from typing import Optional
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

DB_PATH = os.getenv("FLOW_BRIDGE_DB", "/var/data/flow_bridge.sqlite3")
TOKEN = os.getenv("FLOW_BRIDGE_TOKEN", "")
app = FastAPI(title="Flow Bridge API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False,
                   allow_methods=["GET", "POST", "OPTIONS"],
                   allow_headers=["Authorization", "Content-Type"])

def now(): return datetime.now(timezone.utc).isoformat()
def dbconn():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30); c.row_factory = sqlite3.Row
    return c
def auth(authorization: Optional[str]):
    if not TOKEN: raise HTTPException(503, "FLOW_BRIDGE_TOKEN is not configured")
    if authorization != f"Bearer {TOKEN}": raise HTTPException(401, "Unauthorized")

@app.on_event("startup")
def startup():
    with dbconn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, prompt TEXT NOT NULL, model TEXT, aspect_ratio TEXT,
          status TEXT NOT NULL, result TEXT, error TEXT, created_at TEXT, updated_at TEXT)""")
        c.execute("CREATE INDEX IF NOT EXISTS jobs_status_created ON jobs(status, created_at)")

class JobIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)
    model: Optional[str] = Field(default=None, max_length=120)
    aspect_ratio: Optional[str] = Field(default=None, max_length=30)
class StatusIn(BaseModel):
    status: str = Field(pattern="^(queued|running|completed|failed|cancelled)$")
    result: Optional[str] = Field(default=None, max_length=20000)
    error: Optional[str] = Field(default=None, max_length=4000)

@app.get("/api/health")
def health(): return {"ok": True, "service": "flow-bridge", "version": "0.1.0"}

@app.post("/api/jobs")
def create_job(p: JobIn, authorization: Optional[str] = Header(default=None)):
    auth(authorization); jid = str(uuid.uuid4()); t = now()
    with dbconn() as c:
        c.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)",
                  (jid,p.prompt,p.model,p.aspect_ratio,"queued",None,None,t,t))
    return {"id":jid,"status":"queued","created_at":t}

@app.get("/api/next")
def next_job(authorization: Optional[str] = Header(default=None)):
    auth(authorization)
    with dbconn() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
        if row is None: c.commit(); return {"job":None}
        t=now(); c.execute("UPDATE jobs SET status='running',updated_at=? WHERE id=?",(t,row["id"])); c.commit()
    job=dict(row); job.update(status="running",updated_at=t)
    return {"job":job}

@app.post("/api/jobs/{job_id}/status")
def update_job(job_id: str, p: StatusIn, authorization: Optional[str] = Header(default=None)):
    auth(authorization); t=now()
    with dbconn() as c:
        cur=c.execute("UPDATE jobs SET status=?,result=?,error=?,updated_at=? WHERE id=?",
                      (p.status,p.result,p.error,t,job_id))
        if cur.rowcount == 0: raise HTTPException(404,"Job not found")
    return {"id":job_id,"status":p.status,"updated_at":t}

@app.get("/api/jobs")
def list_jobs(limit: int=25, authorization: Optional[str]=Header(default=None)):
    auth(authorization); limit=max(1,min(limit,100))
    with dbconn() as c:
        rows=c.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",(limit,)).fetchall()
    return {"jobs":[dict(r) for r in rows]}
