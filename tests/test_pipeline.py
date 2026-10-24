import asyncio, logging, sqlite3, time
import pytest
from fastapi.testclient import TestClient
from app import db, llm, pipeline as pl, retrieval as rt, ratelimit
import app.main as m
from . import mocks

CL = "gst was launched in 2001"

@pytest.fixture(autouse=True)
def world(monkeypatch, tmp_path):
    for k, v in dict(GEMINI_API_KEY="GEMKEY", GROQ_API_KEY="GROQKEY", GNEWS_KEY="GNK", GOOGLE_FACTCHECK_KEY="FCK", DB_PATH=str(tmp_path / "t.db")).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(rt, "TRANSPORT", mocks.TRANSPORT); monkeypatch.setattr(llm, "TRANSPORT", mocks.TRANSPORT)
    llm._cool.clear(); mocks.CALLS.clear(); mocks.PROMPTS.clear(); mocks.DOWN.clear(); mocks.GROQ_BAD.clear(); mocks.BADTEXT[0] = False; ratelimit.hits.clear()

def run(c, **k): return asyncio.run(pl.verify(text=c, **k))

# ---- normal path: grounded Gemini ----
def test_static_fact_from_knowledge():  # no search sources, still TRUE (never RUMOUR)
    d = run("the sun rises in the east")
    assert d["verdict"] == "TRUE" and d["basis_label"] == "AI general knowledge (no live source found)"
    assert d["sources"] == [] and d["google_search_url"].startswith("https://www.google.com/search?q=") and d["explanation"] and d["advice"]

def test_current_fact_uses_search():
    d = run("the president of usa is donald trump")
    assert d["verdict"] == "TRUE" and d["basis_label"] == "AI-checked using Google Search" and d["provider"] == "gemini_search"
    assert [s["publisher"] for s in d["sources"]][:2] == ["whitehouse.gov", "reuters.com"] and d["sources"][0]["origin"] == "grounding"  # grounding first, GNews links after

def test_wrong_date_false_with_links_capped():
    d = run(CL)
    assert d["verdict"] == "FALSE" and "1 July 2017" in d["explanation"]
    assert 3 <= len(d["sources"]) <= 5 and {s["origin"] for s in d["sources"]} == {"grounding", "news"}
    assert d["first_reported"] is None and d["recorded_by"]["note"] == "Original recorder not identified"

def test_unverified_when_llm_says_so():
    d = run("zorblax summit")
    assert d["verdict"] == "UNVERIFIED" and d["confidence"] == "low" and d["sources"] == [] and d["google_search_url"]

# ---- WhatsApp-forward checks ----
def test_multi_claim_message_cannot_hide_a_fake_claim():
    d = run("forwarded: GST launched July 2017 and the moon is made of cheese")
    assert d["verdict"] == "PARTLY_TRUE" and [c["verdict"] for c in d["claims"]] == ["TRUE", "FALSE"]

def test_single_outlet_warning_and_confidence_cap():
    d = run("oneblog says new law passed")
    assert "single_outlet" in d["warnings"] and d["confidence"] == "medium"

def test_old_news_and_no_source_warnings_pass_through_and_unknown_dropped():
    d = run("aaj breaking: bridge collapsed")
    assert d["verdict"] == "MISLEADING" and d["warnings"] == ["old_news", "no_source"] and "Forward mat karo" in d["advice"]

def test_default_advice_matches_language():
    assert run("kal se sab ATM band")["advice"].startswith("Forward mat karo")
    assert run(CL)["advice"].startswith("Do NOT forward")

def test_first_reported_only_for_true_news():
    links = [{"publisher": "B", "url": "u2", "published_at": "2017-07-01T02:00:00Z"}, {"publisher": "A", "url": "u1", "published_at": "2017-07-01T01:00:00Z"}, {"publisher": "C", "url": "u3", "published_at": None}]
    assert pl.first_reported(links, "TRUE")["source"] == "A" and pl.first_reported(links, "FALSE") is None and pl.first_reported([], "TRUE") is None

# ---- fallback chain ----
def test_grounded_429_falls_back_to_plain_gemini():
    mocks.DOWN.add("gem_search")
    d = run(CL)
    assert d["provider"] == "gemini_plain" and d["verdict"] == "FALSE" and d["explanation"]
    assert d["basis_label"] == "AI general knowledge (live search unavailable)"
    assert {s["origin"] for s in d["sources"]} == {"news"} and d["google_search_url"]  # links still from GNews
    assert mocks.CALLS == ["gem_search", "gem_plain"]

def test_all_gemini_down_falls_back_to_groq_and_next_groq_model():
    mocks.DOWN.update({"gem_search", "gem_plain"}); mocks.GROQ_BAD.add("openai/gpt-oss-120b")
    d = run(CL)
    assert d["provider"] == "groq" and d["verdict"] == "FALSE" and "live search unavailable" in d["basis_label"]
    assert mocks.CALLS[2:] == ["groq:openai/gpt-oss-120b", "groq:openai/gpt-oss-20b"]

def test_non_json_from_plain_gemini_falls_through():
    mocks.DOWN.add("gem_search"); mocks.BADTEXT[0] = True
    assert run(CL)["provider"] == "groq"

def test_server_busy_only_when_every_provider_fails():
    mocks.DOWN.update({"gem_search", "gem_plain", "groq"})
    with pytest.raises(llm.Busy) as e: run(CL)
    assert len(e.value.reasons) == 3 and db.cache_get(CL) is None  # failure not cached
    r = TestClient(m.app).post("/api/v1/verify", json={"text": CL})
    assert r.status_code == 503 and "busy" in r.json()["detail"].lower()
    mocks.DOWN.clear(); llm._cool.clear()
    assert run(CL)["verdict"] == "FALSE"  # recovers

def test_missing_keys_do_not_crash_chain(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY"); monkeypatch.delenv("GROQ_API_KEY")
    with pytest.raises(llm.Busy) as e: run(CL)
    assert any("GEMINI_API_KEY missing" in r for r in e.value.reasons) and any("GROQ_API_KEY missing" in r for r in e.value.reasons)

def test_cooldown_skips_provider_that_just_said_quota():
    mocks.DOWN.add("gem_search")
    run(CL); run("the sun rises in the east")
    assert mocks.CALLS.count("gem_search") == 1  # 2nd claim didn't waste a call on it

# ---- cache ----
def test_disk_cache_no_second_call_and_ttls():
    d1 = run(CL); n = len(mocks.CALLS)
    d2 = run(CL)
    assert len(mocks.CALLS) == n and d2["cached"] and d2["verdict"] == d1["verdict"]
    exp = sqlite3.connect(db.os.getenv("DB_PATH")).execute("select expires_at from cache").fetchone()[0] - time.time()
    assert 23 * 3600 < exp < 25 * 3600  # grounded answer: 24h
    mocks.DOWN.add("gem_search"); run("the sun rises in the east")
    exp = sqlite3.connect(db.os.getenv("DB_PATH")).execute("select expires_at from cache where key like 'the sun%'").fetchone()[0] - time.time()
    assert 0.9 * 3600 < exp < 1.1 * 3600  # fallback answer: 1h

def test_broken_db_does_not_break_requests(monkeypatch):
    monkeypatch.setenv("DB_PATH", "/proc/nope/x.db")  # unwritable
    assert run(CL)["verdict"] == "FALSE"

def test_identical_concurrent_claims_share_one_call():
    async def both(): return await asyncio.gather(pl.verify(text=CL), pl.verify(text=CL))
    a, b = asyncio.run(both())
    assert mocks.CALLS.count("gem_search") == 1 and a["verdict"] == b["verdict"] and a["request_id"] != b["request_id"]

# ---- end to end through the API: verify -> share link -> feedback ----
def test_api_verify_share_and_feedback():
    c = TestClient(m.app); r = c.post("/api/v1/verify", json={"text": CL}); assert r.status_code == 200
    d = r.json(); g = c.get(f"/api/v1/result/{d['request_id']}")
    assert g.status_code == 200 and g.json()["verdict"] == "FALSE" and "debug" not in g.json()
    assert c.post("/api/v1/feedback", json={"request_id": d["request_id"], "is_correct": True}).status_code == 200

# ---- parsing / safety ----
def test_links_only_http():
    out = pl.pick_links([{"url": "javascript:alert(1)", "title": "x"}, {"url": "https://ok.com/a", "title": "y"}], [], [], 5)
    assert [s["url"] for s in out] == ["https://ok.com/a"]

def test_parse_rejects_bad_verdict_and_non_json():
    bad = lambda t: {"candidates": [{"content": {"parts": [{"text": t}]}}]}
    for t in ('{"verdict":"MAYBE","explanation":"x"}', "no json here", "[1,2]"):
        with pytest.raises(llm.LLMError): llm._parse(bad(t))
    with pytest.raises(llm.LLMError): llm._parse({"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}})
    a, _, _ = llm._parse(bad('chatter {"claim":"c","verdict":"partly true","confidence":"med","explanation":"e","warnings":"oops","claims":"x"} bye'))
    assert a["verdict"] == "PARTLY_TRUE" and a["confidence"] == "medium" and a["warnings"] == [] and a["claims"] == [] and a["source_named"] == "NOT GIVEN"

def test_keywords_and_factcheck_parse():
    assert rt.kw("the president of usa is donald trump") == "president usa donald trump"
    assert rt.kw("gst was launched in 2001") == "gst launched 2001"
    j = {"claims": [{"text": "x", "claimReview": [{"url": "https://u", "title": "T", "reviewDate": "2024-01-01"}]}]}
    assert rt._fc(j)[0]["url"] == "https://u" and rt._fc({}) == []

def test_keys_never_in_logs(caplog):
    caplog.set_level(logging.DEBUG); mocks.DOWN.add("gem_search"); run(CL)
    for k in ("GEMKEY", "GROQKEY", "GNK", "FCK", "apikey=", "key="): assert k not in caplog.text

# ---- stale-knowledge guard: no live search + current fact ----
PRES = "the president of usa is donald trump"

def test_no_search_and_no_evidence_never_gives_false_on_current_fact(monkeypatch):
    monkeypatch.delenv("GNEWS_KEY")  # no evidence -> model answers from STALE memory ("FALSE")
    mocks.DOWN.add("gem_search")
    d = run(PRES)
    assert d["verdict"] == "UNVERIFIED" and d["verdict"] != "FALSE" and d["downgraded_from"] == "FALSE" and d["confidence"] == "low"
    assert d["explanation"].startswith("Live search is unavailable") and "no_live_search" in d["warnings"]
    assert d["claims"] == [] and d["advice"].startswith("Not confirmed") and d["google_search_url"].startswith("https://www.google.com/search")

def test_plain_call_gets_recent_headlines_as_evidence_and_may_say_true():
    mocks.DOWN.add("gem_search")
    d = run(PRES)
    p = [x for k, x in mocks.PROMPTS if k == "gem_plain"][-1]
    assert "<evidence>" in p and "Trump begins second term as US president" in p and "(Reuters)" in p
    assert "may be OUTDATED" in p and "NEVER give a confident FALSE" in p
    assert d["verdict"] == "TRUE" and d["confidence"] == "medium" and "no_live_search" in d["warnings"] and d["downgraded_from"] is None
    assert {s["origin"] for s in d["sources"]} == {"news"}  # links to verify

def test_grounded_prompt_has_no_evidence_block_and_result_is_untouched():
    d = run(PRES)
    p = [x for k, x in mocks.PROMPTS if k == "gem_search"][0]
    assert "Trump begins second term" not in p and d["verdict"] == "TRUE" and "no_live_search" not in d["warnings"]

def test_groq_fallback_gets_same_guard_and_evidence(monkeypatch):
    mocks.DOWN.update({"gem_search", "gem_plain"})
    d = run(PRES); assert d["provider"] == "groq" and d["verdict"] == "TRUE" and d["confidence"] == "medium"
    assert "Trump begins second term" in [x for k, x in mocks.PROMPTS if k == "groq"][-1]
    monkeypatch.delenv("GNEWS_KEY"); db_clear = sqlite3.connect(db.os.getenv("DB_PATH")); db_clear.execute("delete from cache"); db_clear.commit()
    assert run(PRES)["verdict"] == "UNVERIFIED"

def test_true_from_memory_only_on_current_fact_is_downgraded():
    mocks.DOWN.add("gem_search")
    d = run("the current ceo is noevid"); assert d["verdict"] == "UNVERIFIED" and d["downgraded_from"] == "TRUE"

def test_model_time_sensitive_flag_is_enough():
    mocks.DOWN.add("gem_search")
    d = run("lorem tsflag"); assert d["verdict"] == "UNVERIFIED" and d["downgraded_from"] == "FALSE"

def test_hinglish_message():
    mocks.DOWN.add("gem_search")
    assert run("abhi hinglishstale")["explanation"] == "Live search unavailable, ye current fact hai, neeche ke links se verify karo."

def test_old_and_static_claims_are_not_touched_by_the_guard():
    mocks.DOWN.add("gem_search")
    d = run(CL); assert d["verdict"] == "FALSE" and "no_live_search" not in d["warnings"] and d["downgraded_from"] is None
    assert run("the sun rises in the east")["verdict"] == "TRUE"

def test_time_sensitive_detector():
    for t in ["the president of usa is donald trump", "narendra modi is prime minister of india", "who is the current CEO of google",
              "latest iphone has 5 cameras", "aaj breaking news: bridge fell", "in 2026 india won the cup", "भारत के प्रधानमंत्री कौन हैं"]:
        assert pl.is_time_sensitive(t), t
    for t in ["Abraham Lincoln was the 16th president of the USA", "the sun rises in the east", "gst was launched in 2001",
              "the former president visited in 2012", "Chandrayaan-3 landed on 23 August 2023"]:
        assert not pl.is_time_sensitive(t), t

def test_stale_answers_are_cached_only_briefly(monkeypatch):
    monkeypatch.delenv("GNEWS_KEY"); mocks.DOWN.add("gem_search"); run(PRES)
    exp = sqlite3.connect(db.os.getenv("DB_PATH")).execute("select expires_at from cache where key like 'the president%'").fetchone()[0] - time.time()
    assert 0 < exp < 0.2 * 3600
