"""Run the 4 test claims through the REAL pipeline on YOUR machine:   python tools/try_claims.py [--fresh]
(--fresh = ignore the cache and force new provider calls)"""
import asyncio, sys
from pathlib import Path
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows console
except Exception: pass
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv; load_dotenv(ROOT / ".env")
from app.pipeline import verify
from app import llm

C = [("the sun rises in the east", "TRUE"), ("the president of usa is donald trump", "TRUE (current fact, needs live search)"),
     ("gst was launched in 2001", "FALSE - right date is 1 July 2017"), ("zorblax summit", "UNVERIFIED")]

async def main():
    for c, want in C:
        print(f"\n> {c!r}   expected: {want}")
        try: d = await verify(text=c, debug="--fresh" in sys.argv)
        except llm.Busy as e:
            print("  SERVER BUSY - every provider failed:")
            for r in e.reasons: print("   -", r)
            continue
        print(f"  {d['verdict']} ({d['confidence']}) | {d['basis_label']} | via {d['provider_label']}{' | cached' if d.get('cached') else ''}")
        print("  ", d["explanation"]); print("   next:", d["advice"])
        if d["warnings"]: print("   warnings:", ", ".join(d["warnings"]))
        for s in d["sources"]: print(f"   - [{s['origin']}] {s['title'][:60]} | {s['publisher']} | {s['url'][:60]}")
        print("   google:", d["google_search_url"])
asyncio.run(main())
