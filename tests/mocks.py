"""SIMULATED Gemini / Groq / GNews / Fact Check (offline). Tests the plumbing, NOT real model judgment."""
import json, httpx
CALLS = []        # "gem_search" | "gem_plain" | "groq:<model>"
DOWN = set()      # providers that answer 429: gem_search / gem_plain / groq
GROQ_BAD = set()  # groq model ids that answer 404
BADTEXT = [False] # plain Gemini answers non-JSON
PROMPTS = []      # (kind, prompt) of every Gemini/Groq request
R = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/"
PRES = [("https://www.reuters.com/world/us/trump-second-term", "Trump begins second term as US president", "2025-01-20T17:00:00Z"),
        ("https://www.bbc.com/news/world-us-canada-president", "President Trump signs executive orders", "2025-01-21T09:00:00Z")]
GST = [("https://www.livemint.com/news/gst-launch", "GST launched at midnight 1 July 2017", "2017-07-01T01:00:00Z"),
       ("https://www.thehindu.com/business/gst-comes-into-effect/article1.ece", "GST comes into effect from July 1", "2017-07-01T02:00:00Z"),
       ("https://www.ndtv.com/business/gst-rollout", "GST rollout: what changes", "2017-07-01T03:00:00Z")]

def answer(claim, kind="gem_search", prompt=""):
    c = claim.lower()
    d = dict(verdict="UNVERIFIED", confidence="low", explanation="No evidence of any 'zorblax summit' found.", chunks=[], extra={})
    if "sun rises" in c: d.update(verdict="TRUE", confidence="high", explanation="The sun rises in the east because Earth rotates west to east.", extra={"advice": "Seems okay."})
    elif "president of usa" in c:
        if kind == "gem_search": d.update(verdict="TRUE", confidence="high", explanation="Donald Trump is the current US president (second term).", chunks=[("a1", "whitehouse.gov"), ("a2", "reuters.com")])
        elif "Trump begins second term" in prompt: d.update(verdict="TRUE", confidence="high", explanation="Recent headlines show Trump is president.", extra={"evidence_used": True, "time_sensitive": True})
        else: d.update(verdict="FALSE", confidence="high", explanation="Joe Biden is the US president.")  # STALE memory, the bug being fixed
    elif "cheese" in c: d.update(verdict="TRUE", confidence="high", explanation="One part is true, one is not.", chunks=[("m1", "reuters.com"), ("m2", "thehindu.com")],
        extra={"claims": [{"claim": "GST launched July 2017", "verdict": "TRUE", "note": "ok"}, {"claim": "Moon is made of cheese", "verdict": "FALSE", "note": "it is rock"}]})
    elif "oneblog" in c: d.update(verdict="TRUE", confidence="high", explanation="Only one blog says so.", chunks=[("c1", "oneblog.com"), ("c2", "oneblog.com")])
    elif "aaj breaking" in c: d.update(verdict="MISLEADING", confidence="medium", explanation="This is old news from 2019.", extra={"warnings": ["old_news", "no_source", "bogus"], "advice": "Forward mat karo - purani khabar hai."})
    elif "kal se" in c: d.update(verdict="FALSE", confidence="high", explanation="Aisa koi order nahi hai.", extra={"lang": "hinglish"})
    elif "tsflag" in c: d.update(verdict="FALSE", confidence="high", explanation="stale answer", extra={"time_sensitive": True})
    elif "noevid" in c: d.update(verdict="TRUE", confidence="high", explanation="from memory only")
    elif "hinglishstale" in c: d.update(verdict="FALSE", confidence="high", explanation="purana jawab", extra={"lang": "hinglish"})
    elif "gst" in c: d.update(verdict="FALSE", confidence="high", explanation="GST was launched on 1 July 2017, not 2001.", chunks=[("b1", "livemint.com"), ("b2", "pib.gov.in")])
    return d

def q429():  # shaped like a real Gemini quota error
    return httpx.Response(429, json={"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
        "message": "Quota exceeded for metric generate_content_free_tier_requests, limit: 0",
        "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "30s"}]}})

def _claim(prompt): return prompt.rsplit("<data>", 1)[1].split("</data>")[0]
def _txt(claim, kind="gem_search", prompt=""):
    d = answer(claim, kind, prompt)
    return json.dumps({"lang": "en", "claim": claim, "verdict": d["verdict"], "confidence": d["confidence"], "explanation": d["explanation"], **d["extra"]})

def gem(req):
    body = json.loads(req.content); kind = "gem_search" if "tools" in body else "gem_plain"; CALLS.append(kind)
    if kind in DOWN: return q429()
    prompt = body["contents"][0]["parts"][0]["text"]; PROMPTS.append((kind, prompt)); claim = _claim(prompt)
    chunks = answer(claim, kind, prompt)["chunks"] if kind == "gem_search" else []
    txt = _txt(claim, kind, prompt)
    if "sun rises" in claim: txt = "```json\n" + txt + "\n```"  # fenced output must parse
    if BADTEXT[0] and kind == "gem_plain": txt = "Sorry, I cannot help with that."
    cand = {"content": {"parts": [{"text": txt}]}, "finishReason": "STOP"}
    if chunks: cand["groundingMetadata"] = {"webSearchQueries": ["q"], "groundingChunks": [{"web": {"uri": R + u, "title": t}} for u, t in chunks]}
    return httpx.Response(200, json={"candidates": [cand]})

def groq(req):
    body = json.loads(req.content); m = body["model"]; CALLS.append("groq:" + m)
    if "groq" in DOWN: return httpx.Response(429, json={"error": {"message": "rate limit reached"}})
    if m in GROQ_BAD: return httpx.Response(404, json={"error": {"message": "model_not_found"}})
    assert req.headers["authorization"].startswith("Bearer ")
    prompt = body["messages"][1]["content"]; PROMPTS.append(("groq", prompt))
    return httpx.Response(200, json={"choices": [{"message": {"content": _txt(_claim(prompt), "groq", prompt)}}]})

def handler(req):
    h, q = req.url.host, req.url.params.get("q") or req.url.params.get("query") or ""
    if h == "generativelanguage.googleapis.com": return gem(req)
    if h == "api.groq.com": return groq(req)
    if h == "gnews.io":
        arts = GST if "gst" in q.lower() else PRES if ("president" in q.lower() or "trump" in q.lower()) else []
        return httpx.Response(200, json={"totalArticles": len(arts), "articles": [{"url": u, "title": t, "description": t, "publishedAt": d} for u, t, d in arts]})
    if h == "factchecktools.googleapis.com": return httpx.Response(200, json={})
    return httpx.Response(404)

TRANSPORT = httpx.MockTransport(handler)
