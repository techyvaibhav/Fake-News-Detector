"""Fake GNews / Fact Check / article pages (offline). Only for plumbing tests."""
import httpx
GST = [("https://pib.gov.in/PressReleasePage.aspx?PRID=1", "GST rolled out across India from 1 July 2017", "2017-07-01T00:00:00Z"),
       ("https://www.livemint.com/news/gst-launch", "GST launched at midnight 1 July 2017: what changes", "2017-07-01T01:00:00Z"),
       ("https://www.thehindu.com/business/gst-comes-into-effect/article1.ece", "GST comes into effect from July 1", "2017-07-01T02:00:00Z")]

def handler(req):
    h, q = req.url.host, req.url.params.get("q") or req.url.params.get("query") or ""
    if h == "gnews.io":
        arts = GST if "gst" in q.lower() else []
        return httpx.Response(200, json={"totalArticles": len(arts), "articles": [
            {"url": u, "title": t, "description": t, "publishedAt": d} for u, t, d in arts]})
    if h == "factchecktools.googleapis.com":
        return httpx.Response(200, json={})  # real API returns {} when nothing fact-checked
    if h.endswith(("pib.gov.in", "livemint.com", "thehindu.com")): return httpx.Response(200, text="ok")
    return httpx.Response(404)  # RSS feeds etc: unreachable in test -> counted as failed feeds

TRANSPORT = httpx.MockTransport(handler)

def fake_llm(mp, llm, claim, stance, queries, same=True):
    async def ex(t): return {"claim": claim, "verifiable": True, "sensitive": False, "queries": queries}
    async def cl(c, docs): return [{"i": d["i"], "stance": stance, "same_event": same, "debunk": False} for d in docs]
    async def exp(*a): return "explanation"
    mp.setattr(llm, "extract_claim", ex); mp.setattr(llm, "classify", cl); mp.setattr(llm, "explain", exp)
