"""Extra links only (Fact Check API + GNews, 1 call each). Results NEVER decide a verdict."""
import asyncio, logging, os, re, time
import httpx

log = logging.getLogger("fnd.retrieval")
for n in ("httpx", "httpcore"):  # httpx logs full URLs incl. ?key=/apikey= -> keep quiet
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

def _redact(s):
    for k in ("GOOGLE_FACTCHECK_KEY", "GNEWS_KEY", "GEMINI_API_KEY", "GROQ_API_KEY"):
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
            if not docs: tr["note"] = _redact(r.text[:120])
    except Exception as e:
        tr["err"] = type(e).__name__  # no URL in msg: it holds the key
    tr["ms"] = int((time.time() - t0) * 1000)
    log.info("%-9s status=%s n=%s %sms q=%r%s", src, tr["status"], tr["n"], tr["ms"], q,
             f" ERR={tr['err']}" if tr["err"] else (f" empty-body={tr['note']}" if tr["note"] else ""))
    return docs, tr

def _fc(j):
    return [{"url": rv["url"], "title": rv.get("title") or cl.get("text", ""), "date": rv.get("reviewDate"),
             "snippet": f'{cl.get("text", "")} -> rating: {rv.get("textualRating", "")}'}
            for cl in j.get("claims", []) for rv in cl.get("claimReview", []) if rv.get("url")]

def _gn(j):
    return [{"url": a["url"], "title": a["title"], "date": a.get("publishedAt"), "snippet": a.get("description") or ""}
            for a in j.get("articles", []) if a.get("url")]

def _miss(src, q, name): return [], {"src": src, "q": q, "status": None, "n": 0, "err": f"{name} missing", "note": None, "ms": 0}

async def fact_check(c, q):
    k = os.getenv("GOOGLE_FACTCHECK_KEY")
    if not k: return _miss("factcheck", q, "GOOGLE_FACTCHECK_KEY")
    return await _get(c, "factcheck", "https://factchecktools.googleapis.com/v1alpha1/claims:search",
                      {"query": q, "key": k, "pageSize": 8}, q, _fc)

async def gnews(c, q):
    k = os.getenv("GNEWS_KEY")
    if not k: return _miss("gnews", q, "GNEWS_KEY")
    lang = "hi" if re.search("[\u0900-\u097F]", q) else "en"
    return await _get(c, "gnews", "https://gnews.io/api/v4/search",
                      {"q": q, "apikey": k, "max": 10, "country": "in", "lang": lang}, q, _gn)

async def extras(q):
    """1 Fact Check + 1 GNews call in parallel -> (fc_docs, news_docs, trace)."""
    if not q: return [], [], []
    async with httpx.AsyncClient(timeout=10, headers=H, follow_redirects=True, transport=TRANSPORT) as c:
        (fd, ft), (nd, nt) = await asyncio.gather(fact_check(c, q), gnews(c, q))
    return fd, nd, [ft, nt]
