import importlib.util
import os
import uuid

import pytest
from fastapi.testclient import TestClient

SPEC = importlib.util.spec_from_file_location("flow_bridge_server", os.path.join(os.path.dirname(__file__), "..", "server.py"))
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DB_PATH", str(tmp_path / "test.sqlite3"))
    monkeypatch.setattr(server, "TOKEN", "test-secret")
    monkeypatch.setattr(server, "LEASE", 60)
    monkeypatch.setattr(server, "RETRIES", 1)
    monkeypatch.setattr(server, "RETRY_DELAY", 5)
    server.startup()
    with TestClient(server.app) as c:
        yield c

def headers(worker="iphone-test"):
    return {"Authorization": "Bearer test-secret", "X-Worker-ID": worker}

def test_health_and_auth(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/jobs").status_code == 401

def test_submit_claim_heartbeat_complete_and_history(client):
    r = client.post("/api/jobs", headers=headers(), json={"prompt":"a lighthouse","mode":"image"})
    assert r.status_code == 200
    job_id = r.json()["id"]
    claimed = client.get("/api/next", headers=headers()).json()["job"]
    assert claimed["id"] == job_id and claimed["status"] == "running"
    hb = client.post(f"/api/jobs/{job_id}/heartbeat", headers=headers(), json={"progress":45})
    assert hb.status_code == 200
    done = client.post(f"/api/jobs/{job_id}/status", headers=headers(), json={
        "status":"completed","progress":100,"result":{"result_url":"https://example.com/image.png"}})
    assert done.status_code == 200 and done.json()["status"] == "completed"
    assert client.get("/api/jobs", headers=headers()).json()["counts"]["completed"] == 1

def test_worker_lease_is_enforced(client):
    job = client.post("/api/jobs", headers=headers(), json={"prompt":"test"}).json()
    client.get("/api/next", headers=headers())
    wrong = client.post(f"/api/jobs/{job['id']}/heartbeat",
                        headers=headers("other-worker"), json={"progress":10})
    assert wrong.status_code == 409

def test_pause_blocks_claim_and_resume_allows_it(client):
    client.post("/api/jobs", headers=headers(), json={"prompt":"paused"})
    assert client.post("/api/queue/control", headers=headers(), json={"action":"pause"}).status_code == 200
    assert client.get("/api/next", headers=headers()).json()["job"] is None
    client.post("/api/queue/control", headers=headers(), json={"action":"resume"})
    assert client.get("/api/next", headers=headers()).json()["job"] is not None

def test_cancel_and_retry(client):
    job = client.post("/api/jobs", headers=headers(), json={"prompt":"retry me"}).json()
    assert client.post(f"/api/jobs/{job['id']}/cancel", headers=headers()).status_code == 200
    assert client.post(f"/api/jobs/{job['id']}/retry", headers=headers()).status_code == 200

def test_validation_rejects_invalid_reference_url(client):
    r = client.post("/api/jobs", headers=headers(), json={
        "prompt":"test","references":[{"name":"bad","url":"javascript:alert(1)"}]})
    assert r.status_code == 422
