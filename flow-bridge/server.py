import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, field_validator

APP_VERSION = "1.0.0"
DB_PATH = os.getenv("FLOW_BRIDGE_DB", "/var/data/flow_bridge.sqlite3")
TOKEN = os.getenv("FLOW_BRIDGE_TOKEN", "")
CORS_ORIGINS = [x.strip() for x in os.getenv("FLOW_BRIDGE_CORS_ORIGINS", "*").split(",") if x.strip()]
LEASE_SECONDS = max(30, min(int(os.getenv("FLOW_BRIDGE_LEASE_SECONDS", "120")), 900))
MAX_BODY_BYTES = 12 * 1024 * 1024
RATE_LIMIT = max(30, int(os.getenv("FLOW_BRIDGE_RATE_PER_MINUTE", "120")))
rate_buckets: dict[str, list[float]] = {}

app = FastAPI(title="Flow Bridge API", version=APP_VERSION,
              description="Persistent coordination queue for an authenticated browser-side Google Flow worker.")
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=False,
                   allow_methods=["GET", "POST", "OPTIONS"],
                   allow_headers=["Authorization", "Content-Type"])

def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()

def epoch() -> float:
    return time.time()

def dbconn() -> sqlite3.Connection:
    path = Path(DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def init_db() -> None:
    with dbconn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, prompt TEXT NOT NULL, model TEXT, aspect_ratio TEXT,
          status TEXT NOT NULL, result TEXT, error TEXT, created_at TEXT, updated_at TEXT)""")
        existing = {r["name"] for r in c.execute("PRAGMA table_info(jobs)").fetchall()}
        additions = {
            "owner": "TEXT NOT NULL DEFAULT 'default'", "kind": "TEXT NOT NULL DEFAULT 'video'",
            "options_json": "TEXT NOT NULL DEFAULT '{}'", "references_json": "TEXT NOT NULL DEFAULT '[]'",
            "metadata_json": "TEXT NOT NULL DEFAULT '{}'", "progress": "INTEGER NOT NULL DEFAULT 0",
            "progress_message": "TEXT", "worker_id": "TEXT", "lease_token": "TEXT", "lease_until": "REAL",
            "attempts": "INTEGER NOT NULL DEFAULT 0", "max_attempts": "INTEGER NOT NULL DEFAULT 2",
            "next_attempt_at": "REAL NOT NULL DEFAULT 0", "cancel_requested": "INTEGER NOT NULL DEFAULT 0",
            "started_at": "TEXT", "completed_at": "TEXT",
        }
        for name, ddl in additions.items():
            if name not in existing:
                c.execute("ALTER TABLE jobs ADD COLUMN " + name + " " + ddl)
        c.execute("CREATE INDEX IF NOT EXISTS jobs_status_created ON jobs(status, created_at)")
        c.execute("CREATE INDEX IF NOT EXISTS jobs_claim ON jobs(status, next_attempt_at, created_at)")
        c.execute("""CREATE TABLE IF NOT EXISTS queue_control (
          id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL DEFAULT 0,
          stopped INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL)""")
        c.execute("INSERT OR IGNORE INTO queue_control(id,paused,stopped,updated_at) VALUES(1,0,0,?)", (utcnow(),))

@app.on_event("startup")
def startup() -> None:
    init_db()

@app.middleware("http")
async def request_guard(request: Request, call_next):
    if request.method != "OPTIONS":
        ip = request.client.host if request.client else "unknown"
        now = epoch()
        recent = [t for t in rate_buckets.get(ip, []) if now - t < 60]
        if len(recent) >= RATE_LIMIT:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
        recent.append(now)
        rate_buckets[ip] = recent
        if len(rate_buckets) > 5000:
            for key in list(rate_buckets)[:1000]:
                rate_buckets.pop(key, None)
        if int(request.headers.get("content-length", "0") or "0") > MAX_BODY_BYTES:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=413, content={"detail": "Request body too large"})
    return await call_next(request)

def require_auth(authorization: Optional[str]) -> str:
    if not TOKEN:
        raise HTTPException(status_code=503, detail="FLOW_BRIDGE_TOKEN is not configured")
    expected = "Bearer " + TOKEN
    if not hmac.compare_digest((authorization or "").encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return hashlib.sha256(TOKEN.encode()).hexdigest()[:16]

def json_load(value: Optional[str], fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, ValueError):
        return fallback

def job_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for column, key, fallback in (("options_json","options",{}),("references_json","references",[]),
                                  ("metadata_json","metadata",{})):
        item[key] = json_load(item.pop(column, None), fallback)
    item["result"] = json_load(item["result"], None) if item.get("result") else None
    item.pop("lease_token", None)
    item.pop("owner", None)
    item.pop("cancel_requested", None)
    return item

class JobIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=12000)
    kind: str = Field(default="video", pattern="^(image|video)$")
    model: Optional[str] = Field(default=None, max_length=120)
    aspect_ratio: Optional[str] = Field(default=None, max_length=20)
    options: dict[str, Any] = Field(default_factory=dict)
    references: list[str] = Field(default_factory=list, max_length=8)
    max_attempts: int = Field(default=2, ge=1, le=5)

    @field_validator("prompt")
    @classmethod
    def prompt_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must not be blank")
        return value.strip()

    @field_validator("references")
    @classmethod
    def references_are_bounded(cls, values: list[str]) -> list[str]:
        for value in values:
            if len(value) > 8_000_000:
                raise ValueError("each reference must be at most 8 MB encoded")
            if not value.startswith("data:image/"):
                raise ValueError("references must be image data URLs")
        return values

class StatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str = Field(pattern="^(running|completed|failed|cancelled)$")
    worker_id: str = Field(min_length=4, max_length=100)
    lease_token: str = Field(min_length=20, max_length=120)
    progress: int = Field(default=0, ge=0, le=100)
    message: Optional[str] = Field(default=None, max_length=1000)
    result: Optional[dict[str, Any]] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = Field(default=None, max_length=4000)

class HeartbeatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker_id: str = Field(min_length=4, max_length=100)
    lease_token: str = Field(min_length=20, max_length=120)
    progress: Optional[int] = Field(default=None, ge=0, le=100)
    message: Optional[str] = Field(default=None, max_length=1000)

class ControlIn(BaseModel):
    action: str = Field(pattern="^(pause|resume|stop)$")

def recover_expired(c: sqlite3.Connection) -> None:
    now_epoch, now_text = epoch(), utcnow()
    c.execute("""UPDATE jobs
      SET status=CASE WHEN attempts < max_attempts THEN 'queued' ELSE 'failed' END,
          error=CASE WHEN attempts < max_attempts THEN 'Worker lease expired; queued for retry'
                     ELSE 'Worker lease expired; retry limit reached' END,
          worker_id=NULL, lease_token=NULL, lease_until=NULL, next_attempt_at=?, updated_at=?
      WHERE status='running' AND lease_until IS NOT NULL AND lease_until < ?""",
      (now_epoch + 5, now_text, now_epoch))

@app.get("/api/health")
def health():
    with dbconn() as c:
        counts = {r["status"]: r["n"] for r in c.execute("SELECT status,COUNT(*) n FROM jobs GROUP BY status")}
        control = c.execute("SELECT paused,stopped FROM queue_control WHERE id=1").fetchone()
    return {"ok":True,"service":"flow-bridge","version":APP_VERSION,
            "queue":{k:counts.get(k,0) for k in ("queued","running","completed","failed","cancelled")},
            "paused":bool(control["paused"]),"stopped":bool(control["stopped"])}

@app.post("/api/jobs", status_code=201)
def create_job(p: JobIn, authorization: Optional[str] = Header(default=None)):
    owner = require_auth(authorization)
    jid, t = str(uuid.uuid4()), utcnow()
    with dbconn() as c:
        control = c.execute("SELECT stopped FROM queue_control WHERE id=1").fetchone()
        if control and control["stopped"]:
            c.execute("UPDATE queue_control SET stopped=0,updated_at=? WHERE id=1",(t,))
        c.execute("""INSERT INTO jobs(id,prompt,model,aspect_ratio,status,result,error,created_at,updated_at,
          owner,kind,options_json,references_json,max_attempts,next_attempt_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (jid,p.prompt,p.model,p.aspect_ratio,"queued",None,None,t,t,owner,p.kind,
           json.dumps(p.options),json.dumps(p.references),p.max_attempts,0))
    return {"id":jid,"status":"queued","created_at":t}

@app.get("/api/next")
def next_job(worker_id: str = Query(min_length=4,max_length=100),
             authorization: Optional[str] = Header(default=None)):
    owner = require_auth(authorization)
    with dbconn() as c:
        c.execute("BEGIN IMMEDIATE")
        recover_expired(c)
        control = c.execute("SELECT paused,stopped FROM queue_control WHERE id=1").fetchone()
        if control["paused"] or control["stopped"]:
            c.commit()
            return {"job":None,"paused":bool(control["paused"]),"stopped":bool(control["stopped"])}
        row = c.execute("""SELECT * FROM jobs WHERE owner=? AND status='queued' AND next_attempt_at<=?
          ORDER BY created_at ASC LIMIT 1""",(owner,epoch())).fetchone()
        if row is None:
            c.commit()
            return {"job":None,"paused":False,"stopped":False}
        lease = secrets.token_urlsafe(32)
        t = utcnow()
        c.execute("""UPDATE jobs SET status='running',worker_id=?,lease_token=?,lease_until=?,
          attempts=attempts+1,started_at=COALESCE(started_at,?),updated_at=?,progress=0,
          progress_message='Claimed by iPhone browser worker' WHERE id=? AND status='queued'""",
          (worker_id,lease,epoch()+LEASE_SECONDS,t,t,row["id"]))
        claimed = c.execute("SELECT * FROM jobs WHERE id=?",(row["id"],)).fetchone()
        c.commit()
    item = job_dict(claimed)
    item["lease_token"], item["lease_seconds"] = lease, LEASE_SECONDS
    return {"job":item,"paused":False,"stopped":False}

@app.post("/api/jobs/{job_id}/heartbeat")
def heartbeat(job_id: str, p: HeartbeatIn, authorization: Optional[str] = Header(default=None)):
    owner = require_auth(authorization)
    with dbconn() as c:
        cur = c.execute("""UPDATE jobs SET lease_until=?,updated_at=?,progress=COALESCE(?,progress),
          progress_message=COALESCE(?,progress_message)
          WHERE id=? AND owner=? AND status='running' AND worker_id=? AND lease_token=?""",
          (epoch()+LEASE_SECONDS,utcnow(),p.progress,p.message,job_id,owner,p.worker_id,p.lease_token))
        if cur.rowcount != 1:
            raise HTTPException(409,"Worker lease is no longer valid")
    return {"id":job_id,"lease_seconds":LEASE_SECONDS,"updated_at":utcnow()}

@app.post("/api/jobs/{job_id}/status")
def update_job(job_id: str, p: StatusIn, authorization: Optional[str] = Header(default=None)):
    owner, t = require_auth(authorization), utcnow()
    with dbconn() as c:
        row = c.execute("""SELECT attempts,max_attempts FROM jobs WHERE id=? AND owner=?
          AND status='running' AND worker_id=? AND lease_token=?""",
          (job_id,owner,p.worker_id,p.lease_token)).fetchone()
        if row is None:
            raise HTTPException(409,"Job not found or worker lease is no longer valid")
        if p.status == "failed" and row["attempts"] < row["max_attempts"]:
            delay = min(300,5 * (2 ** max(0,row["attempts"]-1)))
            c.execute("""UPDATE jobs SET status='queued',error=?,worker_id=NULL,lease_token=NULL,
              lease_until=NULL,next_attempt_at=?,updated_at=?,progress=?,progress_message=? WHERE id=?""",
              (p.error or "Generation failed",epoch()+delay,t,p.progress,
               "Retry scheduled in " + str(delay) + " seconds",job_id))
            return {"id":job_id,"status":"queued","retry_in":delay,"updated_at":t}
        final = p.status
        c.execute("""UPDATE jobs SET status=?,result=?,error=?,metadata_json=?,progress=?,progress_message=?,
          worker_id=NULL,lease_token=NULL,lease_until=NULL,updated_at=?,
          completed_at=CASE WHEN ? IN ('completed','failed','cancelled') THEN ? ELSE completed_at END
          WHERE id=?""",
          (final,json.dumps(p.result) if p.result is not None else None,p.error,json.dumps(p.metadata),
           100 if final=="completed" else p.progress,p.message or final,t,final,t,job_id))
    return {"id":job_id,"status":final,"updated_at":t}

@app.get("/api/jobs")
def list_jobs(limit: int=Query(default=25,ge=1,le=100),
              status: Optional[str]=Query(default=None,pattern="^(queued|running|completed|failed|cancelled)$"),
              authorization: Optional[str]=Header(default=None)):
    owner = require_auth(authorization)
    with dbconn() as c:
        if status:
            rows=c.execute("SELECT * FROM jobs WHERE owner=? AND status=? ORDER BY created_at DESC LIMIT ?",(owner,status,limit)).fetchall()
        else:
            rows=c.execute("SELECT * FROM jobs WHERE owner=? ORDER BY created_at DESC LIMIT ?",(owner,limit)).fetchall()
    return {"jobs":[job_dict(r) for r in rows]}

@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, authorization: Optional[str]=Header(default=None)):
    owner=require_auth(authorization)
    with dbconn() as c:
        row=c.execute("SELECT * FROM jobs WHERE id=? AND owner=?",(job_id,owner)).fetchone()
    if row is None:
        raise HTTPException(404,"Job not found")
    return {"job":job_dict(row)}

@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, authorization: Optional[str]=Header(default=None)):
    owner=require_auth(authorization)
    with dbconn() as c:
        cur=c.execute("""UPDATE jobs SET status=CASE WHEN status='running' THEN 'running' ELSE 'cancelled' END,
          cancel_requested=1,error='Cancellation requested',updated_at=?
          WHERE id=? AND owner=? AND status IN ('queued','running')""",(utcnow(),job_id,owner))
        if cur.rowcount==0:
            raise HTTPException(404,"Active job not found")
    return {"id":job_id,"status":"cancellation_requested"}

@app.post("/api/control")
def control_queue(p: ControlIn, authorization: Optional[str]=Header(default=None)):
    require_auth(authorization)
    t=utcnow()
    with dbconn() as c:
        if p.action=="pause":
            c.execute("UPDATE queue_control SET paused=1,stopped=0,updated_at=? WHERE id=1",(t,))
        elif p.action=="resume":
            c.execute("UPDATE queue_control SET paused=0,stopped=0,updated_at=? WHERE id=1",(t,))
        else:
            c.execute("UPDATE queue_control SET paused=1,stopped=1,updated_at=? WHERE id=1",(t,))
            c.execute("UPDATE jobs SET status='cancelled',error='Queue stopped by user',cancel_requested=1,updated_at=? WHERE status='queued'",(t,))
            c.execute("UPDATE jobs SET cancel_requested=1,error='Stop requested',updated_at=? WHERE status='running'",(t,))
    return {"action":p.action,"updated_at":t}

@app.get("/api/worker/state")
def worker_state(authorization: Optional[str]=Header(default=None)):
    require_auth(authorization)
    with dbconn() as c:
        control=c.execute("SELECT paused,stopped FROM queue_control WHERE id=1").fetchone()
        counts={r["status"]:r["n"] for r in c.execute("SELECT status,COUNT(*) n FROM jobs GROUP BY status")}
    return {"paused":bool(control["paused"]),"stopped":bool(control["stopped"]),
            "queue":{k:counts.get(k,0) for k in ("queued","running","completed","failed","cancelled")}}
