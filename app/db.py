"""SQLite (stdlib, free). Stores verdict meta + feedback. NO raw input text (NFR-5)."""
import os, sqlite3, time

def con():
    c = sqlite3.connect(os.getenv("DB_PATH", "data.db"))
    c.executescript("""
    CREATE TABLE IF NOT EXISTS verifications(id TEXT PRIMARY KEY, claim TEXT, verdict TEXT,
      score INT, confidence TEXT, created_at REAL);
    CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY AUTOINCREMENT, verification_id TEXT,
      is_correct INT, comment TEXT, created_at REAL);""")
    return c

def save_ver(rid, d):
    with con() as c:
        c.execute("INSERT OR REPLACE INTO verifications VALUES(?,?,?,?,?,?)",
                  (rid, d.get("claim"), d.get("verdict"), d.get("score"), d.get("confidence"), time.time()))

def has_ver(rid) -> bool:
    with con() as c:
        return c.execute("SELECT 1 FROM verifications WHERE id=?", (rid,)).fetchone() is not None

def save_fb(rid, ok, cmt):
    with con() as c:
        c.execute("INSERT INTO feedback(verification_id,is_correct,comment,created_at) VALUES(?,?,?,?)",
                  (rid, int(ok), (cmt or "")[:500], time.time()))
