"""Eval runner.  python eval/run_eval.py [--limit N] [--only-verified] [--sleep S] [--report] [--reset]
Runs claims through the pipeline (resumable, saves after each claim), then prints
accuracy + confusion matrix. Re-run later to continue (free-tier quotas!)."""
import argparse, asyncio, csv, json, sys, time
from pathlib import Path
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows console
except Exception: pass
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv; load_dotenv()

HERE = Path(__file__).parent
V2C = {"TRUE": "true", "FALSE": "false", "MISLEADING": "misleading", "PARTLY_TRUE": "partly_true",
       "UNVERIFIED": "unverified", "ERROR": "error"}
# gold label -> predicted classes counted as correct (synthetic rumours: LLM should say unverified/false)
OK = {"true": {"true"}, "false": {"false"}, "misleading": {"misleading", "partly_true", "false"},
      "rumour": {"unverified", "false"}}
COLS = ["true", "partly_true", "misleading", "false", "unverified", "error"]

async def run(rows, res, path, sleep):
    from app.pipeline import verify
    from app.llm import Busy
    for r in rows:
        if r["id"] in res and res[r["id"]]["cls"] != "error": continue  # resume: skip done, retry errors
        t0 = time.time()
        try: d = await verify(text=r["claim"])
        except Busy: print("Gemini quota hit (429) - stopping. Re-run later, it resumes."); break
        except Exception as e: d = {"error": str(e)}
        v = d.get("verdict") or "ERROR"
        res[r["id"]] = {"verdict": v, "cls": V2C.get(v, "error"), "score": d.get("score"),
                        "n_src": len(d.get("sources", [])), "sec": round(time.time() - t0, 1),
                        "err": d.get("error")}
        path.write_text(json.dumps(res, indent=1))
        print(f'{r["id"]} gold={r["label"]:<10} pred={v:<14} {res[r["id"]]["sec"]}s', flush=True)
        await asyncio.sleep(sleep)

def pct(a, b): return f"{100 * a / b:.1f}%" if b else "n/a"

def report(rows, res):
    M = {g: {c: 0 for c in COLS} for g in OK}
    n = ok = bn = bok = rn = rok = 0; sec = []; nosrc = 0; miss = []
    for r in rows:
        x = res.get(r["id"])
        if not x: continue
        g, c = r["label"], x["cls"]
        M[g][c] += 1; n += 1; sec.append(x["sec"]); nosrc += x["n_src"] == 0
        good = c in OK[g]; ok += good
        if not good: miss.append((r["id"], g, x["verdict"], x["score"], r["claim"][:60]))
        if g == "rumour": rn += 1; rok += c in OK["rumour"]
        else:  # binary true vs not-true (AC-1): unverified/err count as wrong
            bn += 1; bok += (c == "true") if g == "true" else (c in ("false", "misleading"))
    if not n: print("No results yet."); return
    print(f"\nEvaluated: {n}/{len(rows)}   Strict accuracy: {pct(ok, n)}")
    print(f"AC-1 true-vs-false accuracy: {pct(bok, bn)} ({bok}/{bn})   [target >= 85%]")
    print(f"Synthetic rumours -> unverified/false: {pct(rok, rn)} ({rok}/{rn})")
    print(f"Results with 0 links: {nosrc}/{n}  (verdict + explanation are still shown)")
    sec.sort(); p = lambda q: sec[min(len(sec) - 1, int(q * len(sec)))]
    print(f"Latency p50={p(.5):.1f}s p95={p(.95):.1f}s   [AC-6 p95 <= 40s]")
    print("\nConfusion matrix (rows = gold, cols = predicted)")
    print(f'{"gold":<12}' + "".join(f"{c[:11]:>12}" for c in COLS))
    for g in OK: print(f"{g:<12}" + "".join(f"{M[g][c]:>12}" for c in COLS))
    if miss:
        print("\nMisses:")
        for m in miss: print("  %s gold=%s pred=%s score=%s | %s" % m)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="max NEW claims to run this time")
    ap.add_argument("--only-verified", action="store_true", help="only rows with status=verified")
    ap.add_argument("--sleep", type=float, default=5, help="secs between claims (free-tier RPM)")
    ap.add_argument("--report", action="store_true", help="no API calls, just print report")
    ap.add_argument("--reset", action="store_true", help="delete saved results first")
    ap.add_argument("--csv", default=str(HERE / "claims.csv"))
    ap.add_argument("--res", default=str(HERE / "results.json"))
    a = ap.parse_args()
    rows = list(csv.DictReader(open(a.csv, encoding="utf-8")))
    if a.only_verified: rows = [r for r in rows if r["status"] == "verified"]
    rp = Path(a.res)
    if a.reset and rp.exists(): rp.unlink()
    res = json.loads(rp.read_text()) if rp.exists() else {}
    if not a.report:
        todo = [r for r in rows if r["id"] not in res]
        if a.limit: todo = todo[:a.limit]
        asyncio.run(run(todo, res, rp, a.sleep))
    report(rows, res)

if __name__ == "__main__": main()
