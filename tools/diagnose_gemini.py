"""Find WHY Gemini returns 429 (run on YOUR machine):   python tools/diagnose_gemini.py [model ...]
Steps: key check -> PLAIN call -> GROUNDED (Google Search) call, per model (default: plain_model + model from config.yaml).
Prints status + error body with the key hidden, and a plain-language hint."""
import os, sys, time
from pathlib import Path
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows console
except Exception: pass
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT / ".env")
import httpx
from app.registry import CFG

KEY = os.getenv("GEMINI_API_KEY", "")
BASE = os.getenv("GEMINI_BASE", "https://generativelanguage.googleapis.com")
hide = lambda s: s.replace(KEY, "***") if KEY else s

def hint(code, body):
    b = body.lower()
    if code == 200: return "OK"
    if code == 429 and "limit: 0" in b: return "no free quota at all for this model/tool on your project (limit 0). e.g. Google Search grounding may not be free on 3.x models. Use a model/tool that has free quota, or enable billing"
    if code == 429 and "perday" in b: return "DAILY quota used up (resets around midnight Pacific time)"
    if code == 429 and "perminute" in b: return "per-MINUTE limit - wait the retryDelay and slow down"
    if code == 429: return "quota exhausted - check AI Studio > Rate limits for this model"
    if code in (401, 403): return "key rejected / project denied / API blocked (or your network blocks the host)"
    if code == 400 and "api_key_invalid" in b: return "API key is invalid"
    if code in (400, 404): return "model not available to your key (new users can't use older models) or bad request"
    return "unexpected - see body"

def probe(c, model, grounded):
    body = {"contents": [{"parts": [{"text": "Who is the current US president? One short line."}]}], "generationConfig": {"maxOutputTokens": 600}}
    if grounded: body["tools"] = [{"google_search": {}}]
    t0 = time.time()
    try: r = c.post(f"{BASE}/v1beta/models/{model}:generateContent", params={"key": KEY}, json=body)
    except httpx.HTTPError as e: return {"code": None, "txt": type(e).__name__, "hint": "network error before reaching Google", "s": time.time() - t0}
    txt = r.text
    if r.status_code == 200:
        try:
            cand = r.json()["candidates"][0]; txt = "".join(p.get("text", "") for p in cand["content"]["parts"])[:100]
            txt += f"  [grounding links: {len((cand.get('groundingMetadata') or {}).get('groundingChunks', []))}]"
        except Exception: pass
    return {"code": r.status_code, "txt": hide(txt)[:700], "hint": hint(r.status_code, r.text), "s": time.time() - t0, "retry": r.headers.get("retry-after")}

def main():
    if not KEY: sys.exit("GEMINI_API_KEY missing in .env")
    models = sys.argv[1:] or list(dict.fromkeys([CFG.get("plain_model", "gemini-3.5-flash-lite"), CFG["model"]]))
    res = {}
    with httpx.Client(timeout=45, headers={"User-Agent": "FakeNewsDetector/1.0"}) as c:
        try: r = c.get(f"{BASE}/v1beta/models", params={"key": KEY, "pageSize": 1})
        except httpx.HTTPError as e: sys.exit(f"cannot reach Google: {type(e).__name__}")
        print(f"[key check: models.list] {r.status_code} -> {hint(r.status_code, r.text) if r.status_code != 200 else 'key is valid'}")
        if r.status_code != 200: print("  body:", hide(r.text)[:400])
        for m in models:
            for g in (False, True):
                o = probe(c, m, g); res[(m, g)] = o
                print(f"\n[{'GROUNDED' if g else 'PLAIN   '} {m}] status={o['code']} {o['s']:.1f}s" + (f" retry-after={o['retry']}" if o.get("retry") else ""))
                print("  body:", o["txt"].replace("\n", " ")); print("  ->", o["hint"])
    print("\n==== SUMMARY ====")
    for (m, g), o in res.items(): print(f"{'GROUNDED' if g else 'PLAIN   '} {m:<26} {'OK' if o['code'] == 200 else 'FAIL ' + str(o['code'])}")
    pl = [o["code"] == 200 for (m, g), o in res.items() if not g]; gr = [o["code"] == 200 for (m, g), o in res.items() if g]
    if any(pl) and not any(gr): print("=> Only the GROUNDED (Google Search) call fails; plain Gemini works. The app falls back to plain automatically.")
    elif not any(pl) and not any(gr): print("=> ALL Gemini calls fail: key/project-level problem (quota, billing, region, denied). The Groq fallback carries the load.")
    elif all(pl + gr): print("=> Everything works now - earlier 429s were probably a per-minute burst or the old key/model.")
    else: print("=> Mixed: use the models marked OK in config.yaml (model / plain_model).")

if __name__ == "__main__": main()
