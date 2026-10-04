"""Test GNews + Fact Check API on their own (real calls, run on YOUR machine):
   python tools/check_retrieval.py "gst launched 2001"      # exact query
   python tools/check_retrieval.py --claim "GST was launched in India in 2001"   # claim -> keywords"""
import asyncio, sys, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv; load_dotenv(Path(__file__).resolve().parent.parent / ".env")
import httpx
from app import retrieval as rt

async def main(q):
    print(f"query: {q!r}\n")
    async with httpx.AsyncClient(timeout=15, headers=rt.H) as c:
        for f in (rt.fact_check, rt.gnews):
            docs, tr = await f(c, q)
            print(f"[{tr['src']}] status={tr['status']} results={tr['n']} {tr['ms'] if 'ms' in tr else '-'}ms err={tr['err']}")
            if tr["note"]: print("   empty response body:", tr["note"])
            for d in docs[:3]: print("   -", d["title"][:80], "|", d["url"][:70], "|", d["date"])
            print()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="  log: %(message)s")
    a = sys.argv[1:]
    asyncio.run(main(rt.kw(a[1]) if a[:1] == ["--claim"] else " ".join(a)))
