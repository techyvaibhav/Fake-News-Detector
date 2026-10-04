import logging, time, uuid
import httpx, trafilatura
from . import llm, retrieval as rt
from .registry import lookup, CFG
from .scoring import score, DISC

log = logging.getLogger("fnd.pipeline")
_cache = {}  # key -> (time, result); 30 min TTL (FR-3.4)

async def url_text(u):
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
        r = await c.get(u, headers=rt.H)
    return trafilatura.extract(r.text) or ""

async def verify(text=None, url=None, debug=False):
    rid = str(uuid.uuid4())
    inp = (text or "").strip()
    if url:
        inp = (await url_text(url) or inp)[:2000]
    if not inp:
        return {"error": "Empty input. Send a news claim, headline or URL.", "request_id": rid}
    key = inp[:300]
    if not debug and key in _cache and time.time() - _cache[key][0] < CFG["cache_ttl_s"] * 60:
        return {**_cache[key][1], "request_id": rid}

    cl = await llm.extract_claim(inp)
    if not cl.get("verifiable"):
        return {"claim": cl.get("claim", ""), "verdict": "NOT_VERIFIABLE", "is_rumour": False,
                "explanation": cl.get("reason", "Opinion/prediction/satire - not verifiable."),
                "sources": [], "disclaimer": DISC, "request_id": rid}

    qs = rt.build_queries(cl.get("queries"), cl["claim"])[:CFG.get("max_queries", 5)]
    docs, bad, trace = await rt.gather(qs)
    n_raw = len(docs)  # retrieved before any filtering
    docs = docs[:CFG["max_docs"]]
    for i, d in enumerate(docs): d["i"] = i
    cls = {x["i"]: x for x in await llm.classify(cl["claim"],
           [{k: d[k] for k in ("i", "title", "snippet", "date")} for d in docs])} if docs else {}

    evs = []
    for d in docs:
        x = cls.get(d["i"])
        if not x or x["stance"] == "irrelevant" or not x.get("same_event"): continue
        s = lookup(d["url"])
        st = "contradicts" if (x.get("debunk") and s["type"] == "factcheck") else x["stance"]
        evs.append({"name": s["name"], "url": d["url"], "headline": d["title"], "tier": s["tier"],
                    "type": s["type"], "stance": st, "published_at": d["date"], "grp": rt.grp(d)})

    ok = await rt.alive([e["url"] for e in evs])  # drop dead links
    n_rel = len(evs)
    evs = [e for e, a in zip(evs, ok) if a]
    fun = {"queries": qs, "retrieved": n_raw, "relevant": n_rel, "links_alive": len(evs),
           "failed_calls": bad, "calls": trace}  # funnel: where did results drop?
    log.info("funnel queries=%s retrieved=%d relevant=%d alive=%d failed_calls=%d", qs, n_raw, n_rel, len(evs), bad)

    r = score(evs, CFG, sensitive=cl.get("sensitive", False), failed=bad, n_raw=n_raw)
    dated = sorted([e for e in evs if e["published_at"]], key=lambda e: e["published_at"])
    first = ({"source": dated[0]["name"], "url": dated[0]["url"], "published_at": dated[0]["published_at"]}
             if dated else None)
    if n_raw == 0:  # deterministic text, no LLM (nothing to ground it on)
        exp = (f"No articles could be retrieved for this claim ({bad} search call(s) failed). "
               "It cannot be checked right now - treat it as unverified and check official sources.")
    elif r["is_rumour"] and not evs:
        exp = "NO - This appears to be a rumour. Articles were searched but no trusted (Tier 1/2) source carries this claim."
    else:
        exp = ("NO - This appears to be a rumour. No trusted (Tier 1/2) source carries this claim. "
               if r["is_rumour"] else "") + await llm.explain(cl["claim"], r["verdict"], r["score"], evs)
    out = {"claim": cl["claim"], **r, "explanation": exp, "first_reported": first,
           "recorded_by": {"name": None, "live_url": None, "note": "Original recorder not identified"},
           "sources": [{k: v for k, v in e.items() if k != "grp"} for e in evs],
           "disclaimer": DISC}
    if n_raw: _cache[key] = (time.time(), out)  # never cache a failed/empty search
    return {**out, "request_id": rid, **({"debug": fun} if debug else {})}
