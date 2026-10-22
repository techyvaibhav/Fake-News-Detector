"""LLM-first pipeline. The provider chain (llm.check_claim) decides the verdict, always with explanation + advice.
Grounding links + Fact Check + GNews only supply links; they never change the verdict.
Answers are cached on disk (survives restarts); identical in-flight claims share one call."""
import asyncio, logging, re, time, uuid
from urllib.parse import quote_plus
from . import db, fetch, llm, retrieval as rt
from .registry import lookup, CFG

log = logging.getLogger("fnd.pipeline")
DISC = "This is an AI-assisted estimate. Verify with the linked sources."
PROV = {"gemini_search": "Gemini + Google Search", "gemini_plain": "Gemini (no live search)", "groq": "Groq open model (no live search)"}
BAD = {"FALSE", "MISLEADING"}
_inflight = {}  # key -> Task (dedupe concurrent identical claims)

ADVICE = {  # used only when the model gave no advice line: (english, hinglish)
    "TRUE": ("Looks reliable - still check the linked source before forwarding.", "Sahi lag raha hai - phir bhi forward karne se pehle linked source dekh lo."),
    "FALSE": ("Do NOT forward this. It is false - tell the person who sent it.", "Forward mat karo. Ye galat hai - bhejne wale ko bhi bata do."),
    "MISLEADING": ("Do NOT forward - the context is wrong or the news is old.", "Forward mat karo - context galat ya khabar purani hai."),
    "PARTLY_TRUE": ("Not the whole truth - do not forward until confirmed.", "Poora sach nahi hai - confirm hone tak forward mat karo."),
    "UNVERIFIED": ("Not confirmed - check an official source before forwarding.", "Confirm nahi hua - forward karne se pehle official source se check karo.")}

async def build_input(text, url):
    """-> (input string, meta|None, error|None). URL input: safe fetch + title/date/host (FR-1.2)."""
    txt, meta = (text or "").strip(), None
    if url and url.strip():
        got = await fetch.fetch(url.strip())
        art = fetch.extract(got[1], got[0]) if got else None
        if not art or not art["text"].strip():
            if not txt: return None, None, "Could not read that link (blocked, paywalled or not an article). Paste the text instead."
        else:
            meta = {"host": art["host"], "title": art["title"], "published": art["date"], "url": got[0]}
            head = f"[Article from {art['host']} | title: {art['title'] or 'NOT GIVEN'} | published: {art['date'] or 'NOT GIVEN'}]"
            txt = "\n".join(x for x in (head, txt, art["text"]) if x)
    if not txt: return None, None, "Empty input. Send a news claim, headline, forwarded message or URL."
    return txt[:2000], meta, None

def mk(d, origin):  # one link row; publisher via registry (else domain)
    s = lookup(d["url"])
    return {"title": d.get("title") or s["name"], "publisher": s["name"], "url": d["url"],
            "origin": origin, "tier": s["tier"], "type": s["type"], "published_at": d.get("date")}

def pick_links(ground, fc, gn, k):
    """Mix: grounding first (relevant by construction), then fact-checks, then news. http(s) only."""
    out, seen = [], set()
    for s in ground[:3] + fc[:2] + gn + ground[3:] + fc[2:]:
        if s["url"] in seen or not s["url"].startswith(("http://", "https://")): continue
        seen.add(s["url"]); out.append(s)
        if len(out) == k: break
    return out

def first_reported(links, verdict):
    """Earliest dated link among those found (only for TRUE/PARTLY_TRUE news). Never a guess about who 'really' broke it."""
    if verdict not in ("TRUE", "PARTLY_TRUE"): return None
    d = sorted((l for l in links if l.get("published_at")), key=lambda l: str(l["published_at"]))
    return {"source": d[0]["publisher"], "url": d[0]["url"], "published_at": d[0]["published_at"]} if d else None

def label(prov, ground):
    if prov == "gemini_search":
        return ("search", "AI-checked using Google Search") if ground else ("knowledge", "AI general knowledge (no live source found)")
    return "knowledge", "AI general knowledge (live search unavailable)"

# ---- time-sensitive claims (current office-holders, "latest", recent events) ----
OFFICE = re.compile(r"\b(?:president|prime minister|chief minister|governor|ceo|chief justice|speaker|pm of|cm of)\b", re.I)
PAST = re.compile(r"\b(?:was|were|former|ex|first|second|third|\d{1,2}(?:st|nd|rd|th)|1[0-9]{3}|200[0-9]|201[0-9])\b", re.I)
RECENT = re.compile(r"\b(?:current(?:ly)?|incumbent|latest|recent(?:ly)?|breaking|today|tonight|this (?:week|month|year)|right now|as of|abhi|aaj|202[5-9])\b", re.I)
HI = ("प्रधानमंत्री", "मुख्यमंत्री", "राष्ट्रपति", "वर्तमान", "आज", "अभी", "ताज़ा", "ताजा")
STALE_MSG = {"en": "Live search is unavailable and this is a current fact (it can change), so it cannot be confirmed from stored knowledge. Verify with the links below.",
             "hi": "Live search unavailable, ye current fact hai, neeche ke links se verify karo."}

def is_time_sensitive(text):
    """Safety net next to the model's own time_sensitive flag (office-holder claims without a past marker, recency words)."""
    t = text or ""
    if RECENT.search(t) or any(h in t for h in HI): return True
    return bool(OFFICE.search(t)) and not PAST.search(t)

def finalize(ans, ground, prov, inp):
    """Deterministic guards on top of the model's answer."""
    v, conf, w = ans["verdict"], ans["confidence"], list(ans["warnings"])
    if v == "TRUE" and any(c["verdict"] in BAD for c in ans["claims"]): v = "PARTLY_TRUE"  # a fake claim can't hide behind a true one
    names = {g["title"] for g in ground if g["title"]}
    if ground and len(names) == 1 and v in ("TRUE", "PARTLY_TRUE") and "single_outlet" not in w: w.append("single_outlet")
    if "single_outlet" in w and conf == "high": conf = "medium"
    out = {"verdict": v, "confidence": conf, "warnings": w, "explanation": ans["explanation"], "advice": ans["advice"],
           "claims": ans["claims"], "downgraded_from": None, "stale": False}
    if prov != "gemini_search" and (ans["time_sensitive"] or is_time_sensitive(inp)):
        # no live search + current fact: the model's memory may be outdated -> never a confident TRUE/FALSE from memory
        w.append("no_live_search")
        if v == "TRUE" and ans["evidence_used"]:  # recent headlines directly support it: allow TRUE, but not "high"
            out["confidence"] = "medium" if conf == "high" else conf
        else:
            hing = ans["lang"] in ("hi", "hinglish")
            out.update(verdict="UNVERIFIED", confidence="low", explanation=STALE_MSG["hi" if hing else "en"], advice="", claims=[],
                       downgraded_from=v if v != "UNVERIFIED" else None, stale=True)
    return out

async def _evidence(ex):
    """Recent headlines (Fact Check first, then GNews) for providers WITHOUT live search."""
    try: fc, gn, _ = await ex
    except Exception: return []
    seen, out = set(), []
    for d in fc + gn:
        if d["url"] in seen: continue
        seen.add(d["url"]); out.append({"title": d.get("title") or "", "date": d.get("date"), "pub": lookup(d["url"])["name"], "snip": d.get("snippet")})
    return out[:8]

async def _work(inp, key, debug, meta):
    """Provider chain (verdict) runs while 1 Fact Check + 1 GNews call fetch links + evidence."""
    q = (meta.get("title") if meta and meta.get("title") else inp)[:300]
    ex = asyncio.ensure_future(rt.extras(rt.kw(q, 5)))
    ev = asyncio.ensure_future(_evidence(ex))  # only awaited by providers without live search
    try: ans, ground, gq, prov = await llm.check_claim(inp, evidence=ev)
    except Exception: ex.cancel(); ev.cancel(); raise  # Busy -> API layer ("Server busy")
    try: fc, gn, trace = await ex
    except Exception: fc, gn, trace = [], [], []
    links = pick_links([mk({**x, "date": None}, "grounding") | {"publisher": x["title"] or "web"} for x in ground],
                       [mk(x, "factcheck") for x in fc], [mk(x, "news") for x in gn], CFG["max_links"])
    fin = finalize(ans, ground, prov, inp)
    verdict, conf, warns = fin["verdict"], fin["confidence"], fin["warnings"]
    basis, lab = label(prov, ground)
    claim = ans["claim"] or inp[:200]
    hing = ans["lang"] in ("hi", "hinglish")
    log.info("verdict=%s conf=%s provider=%s links=%d (grounding=%d fc=%d news=%d) warnings=%s downgraded_from=%s", verdict, conf, prov,
             len(links), len(ground), len(fc), len(gn), warns, fin["downgraded_from"])
    out = {"claim": claim, "verdict": verdict, "confidence": conf, "explanation": fin["explanation"],
           "advice": fin["advice"] or ADVICE[verdict][1 if hing else 0], "claims": fin["claims"], "warnings": warns,
           "downgraded_from": fin["downgraded_from"], "first_reported": first_reported(links, verdict),
           "recorded_by": {"name": None, "live_url": None, "note": "Original recorder not identified"},
           "source_named": ans["source_named"], "date_named": ans["date_named"], "lang": ans["lang"],
           "basis": basis, "basis_label": lab, "provider": prov, "provider_label": PROV[prov], "sources": links,
           "google_search_url": "https://www.google.com/search?q=" + quote_plus(claim), "input_meta": meta, "disclaimer": DISC}
    hours = CFG["cache_hours"]["stale"] if fin["stale"] else CFG["cache_hours"]["search" if prov == "gemini_search" else "fallback"]
    db.cache_put(key, out, hours)  # disk cache
    if debug: out = {**out, "debug": {"search_queries": gq, "grounding": len(ground), "factcheck": len(fc), "news": len(gn), "calls": trace}}
    return out

async def verify(text=None, url=None, debug=False):
    rid = str(uuid.uuid4()); now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    inp, meta, err = await build_input(text, url)
    if err: return {"error": err, "request_id": rid}
    key = " ".join(inp.lower().split())[:300]
    hit = None if debug else db.cache_get(key)
    if hit: return {**hit, "request_id": rid, "created_at": now, "cached": True}  # no provider call at all
    if debug: out = await _work(inp, key, True, meta)
    else:
        t = _inflight.get(key)
        if t is None:  # first caller starts the work, identical concurrent callers await the same task
            t = _inflight[key] = asyncio.ensure_future(_work(inp, key, False, meta))
            t.add_done_callback(lambda _t, k=key: _inflight.pop(k, None))
        out = await asyncio.shield(t)
    return {**out, "request_id": rid, "created_at": now}
