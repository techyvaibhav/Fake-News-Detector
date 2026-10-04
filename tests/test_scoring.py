from app.scoring import score
from app.registry import CFG

def ev(tier, typ, st, u):
    return {"tier": tier, "type": typ, "stance": st, "url": u, "grp": u}

def test_rumour():
    assert score([ev(3, "other", "supports", "a")], CFG)["verdict"] == "RUMOUR"
    assert score([], CFG, n_raw=4)["is_rumour"]  # docs retrieved, none Tier1/2

def test_debunk_fake():
    r = score([ev(1, "factcheck", "contradicts", "a")], CFG)
    assert r["verdict"] == "FAKE" and r["score"] <= 14

def test_real_needs_two_independent():
    r = score([ev(1, "wire", "supports", "a"), ev(2, "news", "supports", "b"),
               ev(2, "news", "supports", "c")], CFG)
    assert r["verdict"] == "REAL" and r["score"] < 100

def test_syndication_counts_once():
    a = ev(1, "wire", "supports", "a"); b = {**ev(2, "news", "supports", "b"), "grp": "a"}
    assert score([a, b], CFG)["verdict"] != "REAL"

def test_sensitive_cap():
    r = score([ev(2, "news", "supports", "a"), ev(2, "news", "supports", "b")], CFG, sensitive=True)
    assert r["score"] <= CFG["sensitive_cap"]
