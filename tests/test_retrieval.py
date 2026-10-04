import asyncio, logging
import pytest
from app import retrieval as rt, pipeline as pl, llm
from app.scoring import score
from app.registry import CFG
from . import mocks

def ev(tier, typ, st, u): return {"tier": tier, "type": typ, "stance": st, "url": u, "grp": u}

def test_keywords():
    assert rt.kw("GST was launched in India in 2001") == "gst launched india 2001"

def test_long_query_cut_to_keywords():
    qs = rt.build_queries(["GST was launched in India in 2001 according to a viral forwarded message today"], "GST was launched in 2001")
    assert qs[0] == "gst launched 2001"                       # claim keywords always first
    assert all(len(q.split()) <= 6 for q in qs) and len(qs) == 2

def test_no_retrieval_is_unverified_not_rumour():
    r = score([], CFG, n_raw=0)
    assert r["verdict"] == "UNVERIFIED" and not r["is_rumour"] and r["confidence"] == "LOW"

def test_docs_found_but_no_tier12_is_rumour():
    assert score([], CFG, n_raw=5)["verdict"] == "RUMOUR"
    assert score([ev(3, "other", "supports", "a")], CFG)["verdict"] == "RUMOUR"

def test_tier2_contradiction_is_not_rumour():
    r = score([ev(2, "news", "contradicts", "a")], CFG)
    assert r["verdict"] == "LIKELY_FAKE" and not r["is_rumour"]

def test_factcheck_parse_real_shape():
    j = {"claims": [{"text": "x", "claimReview": [{"url": "https://u", "title": "T", "textualRating": "False", "reviewDate": "2024-01-01"}]}]}
    d = rt._fc(j); assert d[0]["url"] == "https://u" and "False" in d[0]["snippet"]
    assert rt._fc({}) == []  # empty answer = {} , must not crash

@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv("GNEWS_KEY", "GK"); monkeypatch.setenv("GOOGLE_FACTCHECK_KEY", "FK")
    monkeypatch.setattr(rt, "TRANSPORT", mocks.TRANSPORT); pl._cache.clear()
    return monkeypatch

def run(c): return asyncio.run(pl.verify(text=c, debug=True))

def test_pipeline_true_claim(world):
    mocks.fake_llm(world, llm, "GST was rolled out across India on 1 July 2017", "supports", ["GST launch India"])
    d = run("GST was rolled out across India on 1 July 2017")
    assert d["verdict"] == "REAL" and len(d["sources"]) == 3 and d["first_reported"]["source"] == "PIB India"

def test_pipeline_wrong_date_claim(world):
    mocks.fake_llm(world, llm, "GST was launched in 2001", "contradicts", ["GST was launched in India in 2001 as per a viral forwarded message"])
    d = run("gst was launched in 2001")
    assert d["verdict"] == "FAKE" and d["sources"] and d["debug"]["retrieved"] == 3
    assert all(len(q.split()) <= 6 for q in d["debug"]["queries"])

def test_pipeline_nothing_retrieved(world):
    mocks.fake_llm(world, llm, "Zorblax summit held", "supports", ["zorblax summit"])
    d = run("zorblax summit")
    assert d["verdict"] == "UNVERIFIED" and not d["is_rumour"] and d["debug"]["retrieved"] == 0
    assert "key" not in str(d["debug"]).lower() or "missing" not in str(d["debug"])  # no key problem here
    assert not pl._cache  # failed search is not cached

def test_pipeline_docs_but_irrelevant_is_rumour(world):
    mocks.fake_llm(world, llm, "GST abolished tomorrow", "irrelevant", ["gst abolished"], same=False)
    d = run("gst abolished tomorrow")
    assert d["verdict"] == "RUMOUR" and d["debug"]["retrieved"] == 3 and d["debug"]["relevant"] == 0

def test_calls_are_logged(world, caplog):
    caplog.set_level(logging.INFO, logger="fnd.retrieval")
    mocks.fake_llm(world, llm, "GST launched", "supports", [])
    run("GST launched")
    txt = caplog.text
    assert "gnews" in txt and "status=200" in txt and "n=3" in txt and "factcheck" in txt and "GK" not in txt

def test_api_keys_never_in_logs(world, caplog):
    caplog.set_level(logging.DEBUG)  # everything, incl. httpx
    mocks.fake_llm(world, llm, "GST launched", "supports", [])
    run("GST launched")
    assert "GK" not in caplog.text.replace("GKX", "") and "apikey=" not in caplog.text and "key=FK" not in caplog.text
