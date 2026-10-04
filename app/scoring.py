"""Pure scoring logic: evidence list -> score, verdict, confidence."""
from typing import List, Dict

DISC = "This is an AI-assisted estimate. Verify with the linked primary sources."

def indep(evs: List[Dict]) -> List[Dict]:
    # dedupe syndicated copies: one per 'grp' key (content hash/wire id)
    seen, out = set(), []
    for e in evs:
        k = e.get("grp") or e["url"]
        if k not in seen:
            seen.add(k); out.append(e)
    return out

def score(evs: List[Dict], cfg: Dict, sensitive=False, failed=0, n_raw=None):
    """Returns dict(score, verdict, is_rumour, confidence)."""
    uniq = indep(evs)
    n_raw = len(evs) if n_raw is None else n_raw  # docs retrieved before filtering
    sup = [e for e in uniq if e["stance"] == "supports"]
    con = [e for e in uniq if e["stance"] == "contradicts"]
    rel = [e for e in uniq if e["stance"] in ("supports", "neutral")]
    w = cfg["w_tier"]
    # 1) explicit debunk by fact-checker/govt -> FAKE
    if any(e["type"] in ("factcheck", "govt") and e["tier"] == 1 for e in con):
        return _out(8, "FAKE", False, uniq, failed)
    # 2) nothing retrieved at all (search failed / empty) -> UNVERIFIED, never rumour
    if n_raw == 0 and not uniq:
        return _out(40, "UNVERIFIED", False, uniq, failed)
    # 3) rumour: evidence was retrieved but no Tier 1/2 source carries the topic
    if not any(e["tier"] in (1, 2) for e in uniq):
        return _out(10, "RUMOUR", True, uniq, failed)
    s = cfg["base"] + cfg["pts_support"] * sum(w[e["tier"]] for e in sup) \
        - cfg["pts_contra"] * sum(w[e["tier"]] for e in con)
    official = any(e["type"] == "govt" and e["tier"] == 1 for e in sup)
    s = max(1, min(97 if official else 94, s))  # never 0/100 w/o primary source
    if sensitive and not official:
        s = min(s, cfg["sensitive_cap"])
    n12 = sum(1 for e in sup if e["tier"] in (1, 2))
    v = cfg["verdicts"]
    if s >= v["real"] and n12 >= 2: verdict = "REAL"
    elif s >= v["likely_real"]: verdict = "LIKELY_REAL"
    elif s >= v["unverified"]: verdict = "UNVERIFIED"
    else: verdict = "LIKELY_FAKE"  # FAKE only via explicit debunk
    return _out(round(s), verdict, False, uniq, failed)

def _out(s, verdict, rum, uniq, failed):
    n = len(uniq)
    conf = "HIGH" if n >= 4 else "MEDIUM" if n >= 2 else "LOW"
    if failed and conf != "LOW":  # graceful degradation (NFR-9)
        conf = "MEDIUM" if conf == "HIGH" else "LOW"
    return {"score": s, "verdict": verdict, "is_rumour": rum, "confidence": conf}
