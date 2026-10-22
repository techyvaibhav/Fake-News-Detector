"""Check EVERY provider once (5 calls):   python tools/preflight.py
Tells you which of Gemini+Search / Gemini plain / Groq / GNews / Fact Check works with the keys in .env."""
import asyncio, sys
from pathlib import Path
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows console
except Exception: pass
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT / ".env")
import httpx
from app import llm, retrieval as rt

async def main():
    t, ok_llm = "the sun rises in the east", 0
    for name, fn in (("Gemini + Google Search", llm._gem_search), ("Gemini plain", llm._gem_plain), ("Groq", llm._groq)):
        try:
            a, ground, _ = await fn(t); ok_llm += 1
            print(f"OK    {name:<24} verdict={a['verdict']} grounding links={len(ground)}")
        except llm.LLMError as e: print(f"FAIL  {name:<24} [{e.kind}] {e}")
        except Exception as e: print(f"FAIL  {name:<24} {type(e).__name__}")
    async with httpx.AsyncClient(timeout=15, headers=rt.H) as c:
        for f in (rt.fact_check, rt.gnews):
            _, tr = await f(c, "gst launched 2017")
            good = tr["status"] == 200 and not tr["err"]
            print(f"{'OK  ' if good else 'FAIL'}  {tr['src']:<24} status={tr['status']} results={tr['n']}" + (f" err={tr['err']}" if tr["err"] else ""))
    print("\n" + ("READY: at least one LLM provider works." if ok_llm else "NOT READY: no LLM provider works - fix keys/quota above (python tools/diagnose_gemini.py explains Gemini 429s)."))
    sys.exit(0 if ok_llm else 1)

asyncio.run(main())
