"""Flow Bridge: authenticated, lease-based queue for browser-side Flow workers.
Google Flow generation runs in the user's authenticated browser. Google cookies are never stored here.
"""
from __future__ import annotations
import hashlib, json, os, re, secrets, sqlite3, time, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

VERSION = "1.0.0"
DB_PATH = os.getenv("FLOW_BRIDGE_DB", "/var/data/flow_bridge.sqlite3")
TOKEN = os.getenv("FLOW_BRIDGE_TOKEN", "")
ORIGINS = [x.strip() for x in os.getenv("FLOW_BRIDGE_ALLOWED_ORIGINS", "https://labs.google,https://flow.google.com").split(",") if x.strip()]
LEASE = max(60, int(os.getenv("FLOW_BRIDGE_LEASE_SECONDS", "180")))
RETRIES = max(0, min(10, int(os.getenv("FLOW_BRIDGE_MAX_RETRIES", "2"))))
RETRY_DELAY = max(5, int(os.getenv("FLOW_BRIDGE_RETRY_BASE_SECONDS", "20")))
LIMIT = max(10, int(os.getenv("FLOW_BRIDGE_RATE_LIMIT_PER_MINUTE", "60")))
BUCKET: dict[str, list[float]] = {}

app = FastAPI(title="Flow Bridge API", version=VERSION)
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_credentials=False,
                   allow_methods=["GET", "POST", "OPTIONS"],
                   allow_headers=["Authorization", "Content-Type", "X-Worker-ID"])

def now():
    return datetime.now(timezone.utc).isoformat()

def db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=30000")
    c.execute("PRAGMA journal_mode=WAL")
    return c

def auth(header: str | None) -> str:
    if not TOKEN:
        raise HTTPException(503, "FLOW_BRIDGE_TOKEN is not configured")
    if not header or not secrets.compare_digest(header, "Bearer " + TOKEN):
        raise HTTPException(401, "Unauthorized")
    return hashlib.sha256(TOKEN.encode()).hexdigest()

def throttle(request: Request):
    key = request.client.host if request.client else "unknown"
    t = time.monotonic()
    hits = [x for x in BUCKET.get(key, []) if t-x < 60]
    if len(hits) >= LIMIT:
        raise HTTPException(429, "Rate limit exceeded")
    hits.append(t)
    BUCKET[key] = hits

def setting(c, key, default="false"):
    r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default

def set_setting(c, key, value):
    c.execute("INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
              (key, value, now()))

def decode(row):
    d = dict(row)
    for k in ("references_json", "options_json", "result_json"):
        raw = d.pop(k, None)
        d[k[:-5]] = json.loads(raw) if raw else ([] if k.startswith("references") else {})
    return d

def recover(c):
    rows = c.execute("SELECT id,retry_count,max_retries FROM jobs WHERE status='running' AND lease_expires_at < ?", (now(),)).fetchall()
    for r in rows:
        if r["retry_count"] < r["max_retries"]:
            delay = RETRY_DELAY * (2 ** r["retry_count"])
            c.execute("UPDATE jobs SET status='queued',retry_count=retry_count+1,next_attempt_at=?,worker_id=NULL,lease_expires_at=NULL,error='Worker lease expired; requeued',updated_at=? WHERE id=?",
                      (datetime.fromtimestamp(time.time()+delay, timezone.utc).isoformat(), now(), r["id"]))
        else:
            c.execute("UPDATE jobs SET status='failed',worker_id=NULL,lease_expires_at=NULL,error='Worker lease expired; retry limit reached',finished_at=?,updated_at=? WHERE id=?",
                      (now(), now(), r["id"]))

@app.on_event("startup")
def startup():
    c = db()
    c.execute("""CREATE TABLE IF NOT EXISTS jobs(
      id TEXT PRIMARY KEY, owner TEXT NOT NULL, prompt TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'auto',
      model TEXT, aspect_ratio TEXT, status TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
      worker_id TEXT, lease_expires_at TEXT, retry_count INTEGER NOT NULL DEFAULT 0,
      max_retries INTEGER NOT NULL DEFAULT 2, next_attempt_at TEXT,
      references_json TEXT NOT NULL DEFAULT '[]', options_json TEXT NOT NULL DEFAULT '{}',
      result_json TEXT NOT NULL DEFAULT '{}', error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      started_at TEXT, finished_at TEXT)""")
    c.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at TEXT NOT NULL)")
    c.execute("CREATE INDEX IF NOT EXISTS queue_idx ON jobs(owner,status,next_attempt_at,created_at)")
    c.close()

class JobIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)
    mode: str = Field(default="auto", pattern="^(auto|image|video|text_to_image|image_to_video|frames_to_video|ingredients_to_video)$")
    model: str | None = Field(default=None, max_length=120)
    aspect_ratio: str | None = Field(default=None, max_length=20)
    references: list[dict[str, Any]] = Field(default_factory=list, max_length=8)
    options: dict[str, Any] = Field(default_factory=dict)
    max_retries: int | None = Field(default=None, ge=0, le=10)
    @field_validator("references")
    @classmethod
    def refs_valid(cls, refs):
        for r in refs:
            if set(r) - {"name","mime_type","url","kind"}:
                raise ValueError("Reference accepts name, mime_type, url, kind")
            if r.get("url") and not str(r["url"]).startswith(("https://","blob:","data:")):
                raise ValueError("Reference URL must be HTTPS or browser-local")
        return refs

class StatusIn(BaseModel):
    status: str = Field(pattern="^(running|completed|failed|cancelled|queued)$")
    progress: int = Field(default=0, ge=0, le=100)
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = Field(default=None, max_length=4000)

class HeartbeatIn(BaseModel):
    progress: int | None = Field(default=None, ge=0, le=100)
    message: str | None = Field(default=None, max_length=500)

class ControlIn(BaseModel):
    action: str = Field(pattern="^(pause|resume|stop)$")

@app.middleware("http")
async def limits(request: Request, call_next):
    if request.url.path.startswith("/api/") and request.url.path != "/api/health":
        throttle(request)
    return await call_next(request)

@app.get("/api/health")
def health():
    try:
        c=db(); c.execute("SELECT 1"); paused=setting(c,"queue_paused")=="true"; c.close()
        return {"ok":True,"service":"flow-bridge","version":VERSION,"queue_paused":paused,"storage":"sqlite"}
    except Exception:
        raise HTTPException(503,"Database unavailable")

@app.post("/api/jobs")
def create_job(p: JobIn, authorization: str | None=Header(default=None)):
    owner=auth(authorization); jid=str(uuid.uuid4()); ts=now()
    c=db()
    c.execute("""INSERT INTO jobs(id,owner,prompt,mode,model,aspect_ratio,status,max_retries,references_json,options_json,created_at,updated_at)
      VALUES(?,?,?,?,?,?,'queued',?,?,?,?,?)""",
      (jid,owner,p.prompt.strip(),p.mode,p.model,p.aspect_ratio,p.max_retries if p.max_retries is not None else RETRIES,
       json.dumps(p.references),json.dumps(p.options),ts,ts))
    c.close()
    return {"id":jid,"status":"queued","created_at":ts}

@app.get("/api/next")
def next_job(authorization: str | None=Header(default=None), x_worker_id: str | None=Header(default=None)):
    owner=auth(authorization); worker=(x_worker_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{3,100}",worker):
        raise HTTPException(400,"X-Worker-ID header required")
    c=db(); c.execute("BEGIN IMMEDIATE")
    if setting(c,"queue_paused")=="true" or setting(c,"queue_stopped")=="true":
        c.commit(); c.close(); return {"job":None,"queue_paused":setting(db(),"queue_paused")=="true"}
    recover(c)
    r=c.execute("SELECT * FROM jobs WHERE owner=? AND status='queued' AND (next_attempt_at IS NULL OR next_attempt_at<=?) ORDER BY created_at LIMIT 1",(owner,now())).fetchone()
    if r is None:
        c.commit(); c.close(); return {"job":None}
    ts=now(); expiry=datetime.fromtimestamp(time.time()+LEASE,timezone.utc).isoformat()
    c.execute("UPDATE jobs SET status='running',worker_id=?,lease_expires_at=?,started_at=COALESCE(started_at,?),updated_at=? WHERE id=? AND status='queued'",(worker,expiry,ts,ts,r["id"]))
    fresh=c.execute("SELECT * FROM jobs WHERE id=?",(r["id"],)).fetchone()
    c.commit(); result=decode(fresh); c.close()
    return {"job":result}

@app.post("/api/jobs/{job_id}/heartbeat")
def heartbeat(job_id: str,p: HeartbeatIn,authorization: str | None=Header(default=None),x_worker_id: str | None=Header(default=None)):
    owner=auth(authorization); c=db(); ts=now()
    cur=c.execute("UPDATE jobs SET lease_expires_at=?,progress=COALESCE(?,progress),updated_at=? WHERE id=? AND owner=? AND status='running' AND worker_id=?",
      (datetime.fromtimestamp(time.time()+LEASE,timezone.utc).isoformat(),p.progress,ts,job_id,owner,x_worker_id))
    c.close()
    if cur.rowcount!=1: raise HTTPException(409,"Worker does not own this job lease")
    return {"id":job_id,"lease_expires_in":LEASE,"updated_at":ts}

@app.post("/api/jobs/{job_id}/status")
def update_job(job_id: str,p: StatusIn,authorization: str | None=Header(default=None),x_worker_id: str | None=Header(default=None)):
    owner=auth(authorization); c=db()
    r=c.execute("SELECT * FROM jobs WHERE id=? AND owner=?",(job_id,owner)).fetchone()
    if not r: c.close(); raise HTTPException(404,"Job not found")
    if r["status"]=="running" and r["worker_id"]!=x_worker_id: c.close(); raise HTTPException(409,"Worker does not own this job lease")
    if r["status"] in ("completed","failed","cancelled"): c.close(); raise HTTPException(409,"Job is already terminal")
    ts=now(); terminal=p.status in ("completed","failed","cancelled")
    c.execute("UPDATE jobs SET status=?,progress=?,result_json=?,error=?,updated_at=?,finished_at=?,worker_id=?,lease_expires_at=? WHERE id=?",
      (p.status,p.progress,json.dumps(p.result),p.error,ts,ts if terminal else None,None if terminal or p.status=="queued" else r["worker_id"],
       None if terminal or p.status=="queued" else r["lease_expires_at"],job_id))
    if p.status=="failed" and r["retry_count"]<r["max_retries"]:
        delay=RETRY_DELAY*(2**r["retry_count"])
        c.execute("UPDATE jobs SET status='queued',retry_count=retry_count+1,next_attempt_at=?,finished_at=NULL,worker_id=NULL,lease_expires_at=NULL WHERE id=?",
          (datetime.fromtimestamp(time.time()+delay,timezone.utc).isoformat(),job_id))
    fresh=c.execute("SELECT * FROM jobs WHERE id=?",(job_id,)).fetchone(); out=decode(fresh); c.close()
    return out

@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str,authorization: str | None=Header(default=None)):
    owner=auth(authorization); c=db()
    cur=c.execute("UPDATE jobs SET status='queued',retry_count=0,next_attempt_at=NULL,error=NULL,progress=0,worker_id=NULL,lease_expires_at=NULL,finished_at=NULL,updated_at=? WHERE id=? AND owner=? AND status IN ('failed','cancelled')",(now(),job_id,owner))
    c.close()
    if cur.rowcount!=1: raise HTTPException(409,"Only failed or cancelled jobs can be retried")
    return {"id":job_id,"status":"queued"}

@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str,authorization: str | None=Header(default=None)):
    owner=auth(authorization); c=db(); ts=now()
    cur=c.execute("UPDATE jobs SET status='cancelled',worker_id=NULL,lease_expires_at=NULL,updated_at=?,finished_at=? WHERE id=? AND owner=? AND status IN ('queued','running')",(ts,ts,job_id,owner))
    c.close()
    if cur.rowcount!=1: raise HTTPException(409,"Job is not cancellable")
    return {"id":job_id,"status":"cancelled"}

@app.post("/api/queue/control")
def queue_control(p: ControlIn,authorization: str | None=Header(default=None)):
    auth(authorization); c=db()
    set_setting(c,"queue_paused","true" if p.action=="pause" else "false")
    set_setting(c,"queue_stopped","true" if p.action=="stop" else "false")
    c.close()
    return {"action":p.action,"queue_paused":p.action=="pause","queue_stopped":p.action=="stop"}

@app.get("/api/jobs")
def list_jobs(limit: int=Query(default=50,ge=1,le=100),status: str | None=None,authorization: str | None=Header(default=None)):
    owner=auth(authorization)
    if status and status not in ("queued","running","completed","failed","cancelled"): raise HTTPException(400,"Invalid status")
    c=db(); recover(c)
    if status: rows=c.execute("SELECT * FROM jobs WHERE owner=? AND status=? ORDER BY created_at DESC LIMIT ?",(owner,status,limit)).fetchall()
    else: rows=c.execute("SELECT * FROM jobs WHERE owner=? ORDER BY created_at DESC LIMIT ?",(owner,limit)).fetchall()
    counts={r["status"]:r["n"] for r in c.execute("SELECT status,COUNT(*) n FROM jobs WHERE owner=? GROUP BY status",(owner,)).fetchall()}
    paused=setting(c,"queue_paused")=="true"; c.close()
    return {"jobs":[decode(r) for r in rows],"counts":counts,"queue_paused":paused}

@app.get("/api/jobs/{job_id}")
def get_job(job_id: str,authorization: str | None=Header(default=None)):
    owner=auth(authorization); c=db(); r=c.execute("SELECT * FROM jobs WHERE id=? AND owner=?",(job_id,owner)).fetchone(); c.close()
    if not r: raise HTTPException(404,"Job not found")
    return decode(r)
