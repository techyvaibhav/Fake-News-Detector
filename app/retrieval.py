"""Parallel evidence retrieval with per-call logging (free tiers: Fact Check API, GNews, RSS)."""
import asyncio, os, hashlib, logging, re, time
import httpx, feedparser
from .registry import REG

log = logging.getLogger("fnd.retrieval")
for n in ("httpx", "httpcore"):  # httpx logs full URLs incl. ?key=/apikey= -> keep it quiet
    logging.getLogger(n).setLevel(logging.WARNING)
H = {"User-Agent": "FakeNewsDetectorBot/1.0"}
TRANSPORT = None  # tests inject httpx.MockTransport here
STOP = set("a an the is are was were be been of in on at to for from by with and or but not no this that "
           "these those it its as has have had will would can could should said says claim claims news according viral forwarded message".split())
PUNCT = ".,!?;:\"'()[]{}-–—|"

def kw(text, n=5):
    """Keywords only: drop stopwords/punctuation, keep names + numbers, max n unique words."""
    out = []
    for w in text.lower().split():
        w = w.strip(PUNCT)
        if len(w) > 1 and w not in STOP and w not in out: out.append(w)
    return " ".join(out[:n])

def build_queries(qs, claim):
    """Short keyword queries. First = keywords of the claim itself (always works), then LLM
    queries (any query > 6 words is cut down to keywords - never send a full sentence)."""
    out = [kw(claim, 5)]
    for q in qs or []:
        q = " ".join(str(q).split())
        if len(q.split()) > 6: q = kw(q, 5)
        if q and q not in out: out.append(q)
    return [q for q in out if q]

def _redact(s):  # keys must never reach logs
    for k in ("GOOGLE_FACTCHECK_KEY", "GNEWS_KEY", "GEMINI_API_KEY"):
        v = os.getenv(k)
        if v: s = s.replace(v, "***")
    return s

async def _get(c, src, url, prm, q, parse):
    """One API call -> (docs, trace). Never raises; status/count/error go to log + trace."""
    t0, docs = time.time(), []
    tr = {"src": src, "q": q, "status": None, "n": 0, "err": None, "note": None}
    try:
        r = await c.get(url, params=prm); tr["status"] = r.status_code
        if r.status_code != 200: tr["err"] = _redact(r.text[:150])
        else:
            docs = parse(r.json()); tr["n"] = len(docs)
            if not docs: tr["note"] = _redact(r.text[:120])  # why empty? (e.g. totalArticles:0)
    except Exception as e:
        tr["err"] = type(e).__name__  # no URL in msg: it holds the key
    tr["ms"] = int((time.time() - t0) * 1000)
    log.info("%-9s status=%s n=%s %sms q=%r%s", src, tr["status"], tr["n"], tr["ms"], q,
             f" ERR={tr['err']}" if tr["err"] else (f" empty-body={tr['note']}" if tr["note"] else ""))
    return docs, tr

def _fc(j):
    return [{"url": rv["url"], "title": rv.get("title") or cl.get("text", ""),
             "snippet": f'{cl.get("text","")} -> rating: {rv.get("textualRating","")}',
             "date": rv.get("reviewDate")}
            for cl in j.get("claims", []) for rv in cl.get("claimReview", [])]

def _gn(j):
    return [{"url": a["url"], "title": a["title"], "snippet": a.get("description", ""),
             "date": a.get("publishedAt")} for a in j.get("articles", [])]

async def fact_check(c, q):
    k = os.getenv("GOOGLE_FACTCHECK_KEY")
    if not k: return [], {"src": "factcheck", "q": q, "status": None, "n": 0, "err": "GOOGLE_FACTCHECK_KEY missing", "note": None}
    return await _get(c, "factcheck", "https://factchecktools.googleapis.com/v1alpha1/claims:search",
                      {"query": q, "key": k, "pageSize": 8}, q, _fc)

async def gnews(c, q):
    k = os.getenv("GNEWS_KEY")
    if not k: return [], {"src": "gnews", "q": q, "status": None, "n": 0, "err": "GNEWS_KEY missing", "note": None}
    lang = "hi" if re.search("[\u0900-\u097F]", q) else "en"
    return await _get(c, "gnews", "https://gnews.io/api/v4/search",
                      {"q": q, "apikey": k, "max": 10, "country": "in", "lang": lang}, q, _gn)

_rc = {}  # feed cache: url -> (time, entries), 10 min

async def rss_all(c, queries):
    """Free RSS from registry feeds; keep entries matching >=2 query words."""
    words = {w for q in queries for w in q.lower().split() if len(w) > 3}
    async def one(s):
        u = s["rss_url"]
        if u not in _rc or time.time() - _rc[u][0] > 600:
            r = await c.get(u); _rc[u] = (time.time(), feedparser.parse(r.text).entries)
        return [{"url": e.link, "title": e.title, "snippet": e.get("summary", "")[:300], "date": e.get("published")}
                for e in _rc[u][1] if sum(w in (e.get("title", "") + " " + e.get("summary", "")).lower() for w in words) >= 2]
    res = await asyncio.gather(*[one(s) for s in REG.values() if s.get("rss_url")], return_exceptions=True)
    ok = [r for r in res if not isinstance(r, Exception)]
    docs = [d for r in ok for d in r]
    tr = {"src": "rss", "q": "(all feeds)", "status": None, "n": len(docs), "err": f"{len(res)-len(ok)} feeds failed" if len(ok) < len(res) else None, "note": None}
    log.info("rss       feeds_ok=%d/%d n=%d", len(ok), len(res), len(docs))
    return docs, len(res) - len(ok), tr

async def gather(queries):
    """Run all (source x query) calls in parallel -> (docs, n_failed_calls, trace)."""
    async with httpx.AsyncClient(timeout=10, headers=H, follow_redirects=True, transport=TRANSPORT) as c:
        res = await asyncio.gather(*[f(c, q) for q in queries for f in (fact_check, gnews)])
        rd, rb, rtr = await rss_all(c, queries)
    trace = [t for _, t in res] + [rtr]
    bad = sum(1 for t in trace[:-1] if t["err"]) + (1 if rb > 3 else 0)  # many dead feeds -> lower confidence
    docs, seen = [], set()
    for d in [d for ds, _ in res for d in ds] + rd:
        if d["url"] not in seen: seen.add(d["url"]); docs.append(d)
    return docs, bad, trace

async def alive(urls):
    """Drop only really dead links (404/410/5xx/no connection). 403/429 = site blocks bots, link is fine."""
    async with httpx.AsyncClient(timeout=6, headers=H, follow_redirects=True, transport=TRANSPORT) as c:
        async def ok(u):
            st = "err"
            try:
                st = (await c.head(u)).status_code
                if st in (403, 405, 501): st = (await c.get(u)).status_code
            except Exception: pass
            dead = st == "err" or st in (404, 410) or st >= 500
            if dead: log.info("link dead status=%s %s", st, u)
            return not dead
        return await asyncio.gather(*[ok(u) for u in urls])

def grp(d):  # syndication key: normalised title hash
    t = "".join(ch for ch in d["title"].lower() if ch.isalnum())[:60]
    return hashlib.md5(t.encode()).hexdigest()
