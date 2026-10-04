"""In-memory sliding-window limiter (single process; use Redis if you scale out)."""
import time
from collections import defaultdict, deque

hits = defaultdict(deque)  # key -> timestamps

def check(key: str, per_min: int, per_day: int):
    """Returns (allowed, retry_after_seconds)."""
    now, q = time.time(), hits[key]
    while q and now - q[0] > 86400: q.popleft()  # drop >1 day old
    last_min = [t for t in q if now - t < 60]
    if len(q) >= per_day:
        return False, int(86400 - (now - q[0])) + 1
    if len(last_min) >= per_min:
        return False, int(60 - (now - last_min[0])) + 1
    q.append(now)
    return True, 0
