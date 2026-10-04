import sqlite3
from fastapi.testclient import TestClient
from app import db, ratelimit
import app.main as m

def test_ratelimit():
    ratelimit.hits.clear()
    assert all(ratelimit.check("k", 3, 10)[0] for _ in range(3))
    ok, wait = ratelimit.check("k", 3, 10)
    assert not ok and wait > 0

def test_daily_limit():
    ratelimit.hits.clear()
    for _ in range(2): ratelimit.check("d", 99, 2)
    assert not ratelimit.check("d", 99, 2)[0]

def test_feedback_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    ratelimit.hits.clear()
    async def fake(t, u, debug=False): return {"claim": "c", "verdict": "RUMOUR", "score": 10, "confidence": "LOW", "request_id": "r1"}
    monkeypatch.setattr(m, "verify", fake)
    c = TestClient(m.app)
    assert c.post("/api/v1/feedback", json={"request_id": "r1", "is_correct": False}).status_code == 404
    assert c.post("/api/v1/verify", json={"text": "x"}).status_code == 200
    r = c.post("/api/v1/feedback", json={"request_id": "r1", "is_correct": False, "comment": "wrong"})
    assert r.status_code == 200
    n = sqlite3.connect(str(tmp_path / "t.db")).execute("select count(*) from feedback").fetchone()[0]
    assert n == 1

def test_429_http(monkeypatch):
    ratelimit.hits.clear()
    monkeypatch.setitem(m.CFG["rate_limit"], "per_min", 1)
    async def fake(t, u, debug=False): return {"claim": "c", "verdict": "X", "request_id": "r2"}
    monkeypatch.setattr(m, "verify", fake)
    c = TestClient(m.app)
    c.post("/api/v1/verify", json={"text": "x"})
    r = c.post("/api/v1/verify", json={"text": "x"})
    assert r.status_code == 429 and "Retry-After" in r.headers
