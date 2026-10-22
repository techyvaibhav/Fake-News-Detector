import sqlite3
import pytest
from fastapi.testclient import TestClient
from app import db, ratelimit
import app.main as m

ID = "11111111-1111-1111-1111-111111111111"

@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db")); ratelimit.hits.clear()
    seen = {}
    async def fake(t, u, debug=False):
        seen["debug"] = debug
        return {"claim": "c", "verdict": "TRUE", "confidence": "high", "explanation": "e", "request_id": ID}
    monkeypatch.setattr(m, "verify", fake); monkeypatch.setattr(m, "KEYS", set())
    return seen

def test_ratelimit_unit():
    assert all(ratelimit.check("k", 3, 10)[0] for _ in range(3))
    ok, wait = ratelimit.check("k", 3, 10); assert not ok and wait > 0

def test_daily_limit():
    for _ in range(2): ratelimit.check("d", 99, 2)
    assert not ratelimit.check("d", 99, 2)[0]

def test_feedback_flow(tmp_path):
    c = TestClient(m.app)
    assert c.post("/api/v1/feedback", json={"request_id": ID, "is_correct": False}).status_code == 404
    assert c.post("/api/v1/verify", json={"text": "x"}).status_code == 200
    assert c.post("/api/v1/feedback", json={"request_id": ID, "is_correct": False, "comment": "wrong"}).status_code == 200
    assert sqlite3.connect(str(tmp_path / "t.db")).execute("select count(*) from feedback").fetchone()[0] == 1

def test_shared_result_endpoint():
    c = TestClient(m.app); c.post("/api/v1/verify", json={"text": "x"})
    assert c.get(f"/api/v1/result/{ID}").json()["verdict"] == "TRUE"
    assert c.get("/api/v1/result/22222222-2222-2222-2222-222222222222").status_code == 404
    assert c.get("/api/v1/result/not-an-id").status_code == 404

def test_public_rate_limit_is_per_ip_behind_proxy(monkeypatch):
    monkeypatch.setitem(m.CFG["rate_limit"]["public"], "per_min", 1)
    c = TestClient(m.app); A, B = {"x-forwarded-for": "1.1.1.1, 9.9.9.9"}, {"x-forwarded-for": "2.2.2.2"}
    assert c.post("/api/v1/verify", json={"text": "x"}, headers=A).status_code == 200
    r = c.post("/api/v1/verify", json={"text": "x"}, headers=A)
    assert r.status_code == 429 and "Retry-After" in r.headers
    assert c.post("/api/v1/verify", json={"text": "x"}, headers=B).status_code == 200  # other visitor unaffected

def test_api_keys(monkeypatch):
    monkeypatch.setattr(m, "KEYS", {"k1"}); c = TestClient(m.app)
    assert c.post("/api/v1/verify", json={"text": "x"}, headers={"x-api-key": "bad"}).status_code == 401
    assert c.post("/api/v1/verify", json={"text": "x"}, headers={"x-api-key": "k1"}).status_code == 200
    assert c.post("/api/v1/verify", json={"text": "x"}).status_code == 200  # website visitors need no key

def test_input_validation():
    c = TestClient(m.app)
    assert c.post("/api/v1/verify", json={}).status_code == 422
    assert c.post("/api/v1/verify", json={"text": "   "}).status_code == 422
    assert c.post("/api/v1/verify", json={"text": "x" * 2001}).status_code == 422

def test_debug_only_when_allowed(monkeypatch, env):
    c = TestClient(m.app)
    c.post("/api/v1/verify", json={"text": "x", "debug": True}); assert env["debug"] is False
    monkeypatch.setenv("ALLOW_DEBUG", "1"); c.post("/api/v1/verify", json={"text": "x", "debug": True}); assert env["debug"] is True

def test_health_headers_and_website():
    c = TestClient(m.app); r = c.get("/health")
    assert r.json()["ok"] and set(r.json()["providers"]) == {"gemini", "groq", "gnews", "factcheck"}
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    w = c.get("/"); assert w.status_code == 200 and "Fake News Detector" in w.text

def test_db_failure_is_swallowed(monkeypatch):
    monkeypatch.setenv("DB_PATH", "/proc/nope/x.db")
    assert db.cache_get("k") is None and db.get_result("r") is None and db.has_ver("r") is False
    db.save_result("r", {}); db.cache_put("k", {}, 1)  # no exception
