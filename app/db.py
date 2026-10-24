"""SQLite (stdlib, free): verdict log, feedback, answer cache, shared results. Survives restarts if DB_PATH is on a
persistent disk. Never stores raw input text in logs (NFR-5). DB trouble never breaks a request."""
import functools, json, logging, os, sqlite3, time
from contextlib import closing
from pathlib import Path
from .registry import CFG

log = logging.getLogger("fnd.db")
DEF = str(Path(__file__).resolve().parent.parent / "data.db")  # same file whatever the cwd

def con():
    p = os.getenv("DB_PATH", DEF); Path(p).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p, timeout=10)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS verifications(id TEXT PRIMARY KEY, claim TEXT, verdict TEXT,
      score INT, confidence TEXT, created_at REAL);
    CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY AUTOINCREMENT, verification_id TEXT,
      is_correct INT, comment TEXT, created_at REAL);
    CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, value TEXT, expires_at REAL);
    CREATE TABLE IF NOT EXISTS results(id TEXT PRIMARY KEY, value TEXT, created_at REAL);""")
    return c

def safe(default=None):  # a broken/read-only DB must never take the site down
    def deco(f):
        @functools.wraps(f)
        def w(*a, **k):
            try: return f(*a, **k)
            except Exception as e: log.warning("db.%s failed: %s", f.__name__, type(e).__name__); return default
        return w
    return deco

@safe()
def save_ver(rid, d):
    with closing(con()) as c, c:
        c.execute("INSERT OR REPLACE INTO verifications VALUES(?,?,?,?,?,?)",
                  (rid, d.get("claim"), d.get("verdict"), d.get("score"), d.get("confidence"), time.time()))

@safe(False)
def has_ver(rid) -> bool:
    with closing(con()) as c:
        return c.execute("SELECT 1 FROM verifications WHERE id=?", (rid,)).fetchone() is not None

@safe()
def save_fb(rid, ok, cmt):
    with closing(con()) as c, c:
        c.execute("INSERT INTO feedback(verification_id,is_correct,comment,created_at) VALUES(?,?,?,?)",
                  (rid, int(ok), (cmt or "")[:500], time.time()))

@safe()
def cache_get(key):
    with closing(con()) as c:
        row = c.execute("SELECT value FROM cache WHERE key=? AND expires_at>?", (key, time.time())).fetchone()
    return json.loads(row[0]) if row else None

@safe()
def cache_put(key, val, hours):
    with closing(con()) as c, c:
        c.execute("DELETE FROM cache WHERE expires_at<=?", (time.time(),))  # purge old
        c.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?)",
                  (key, json.dumps(val, ensure_ascii=False), time.time() + hours * 3600))

@safe()
def save_result(rid, d):
    with closing(con()) as c, c:
        c.execute("DELETE FROM results WHERE created_at<?", (time.time() - CFG.get("result_days", 30) * 86400,))
        c.execute("INSERT OR REPLACE INTO results VALUES(?,?,?)", (rid, json.dumps(d, ensure_ascii=False), time.time()))

@safe()
def get_result(rid):
    with closing(con()) as c:
        row = c.execute("SELECT value FROM results WHERE id=?", (rid,)).fetchone()
    return json.loads(row[0]) if row else None
