"""Safe article fetch for URL input: SSRF-proof (public IPs only, every redirect re-checked),
robots.txt-aware, size-capped. trafilatura is optional."""
import asyncio, ipaddress, json, socket
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
import httpx

BOT = "FakeNewsDetectorBot"
H = {"User-Agent": f"{BOT}/1.0"}
MAX = 1_000_000   # bytes read per page
TRANSPORT = None  # tests inject httpx.MockTransport

def safe_url(u):
    """http(s) only, and the host must resolve to PUBLIC IPs (blocks localhost / LAN / cloud metadata)."""
    p = urlparse(u or "")
    if p.scheme not in ("http", "https") or not p.hostname: return False
    try: infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except OSError: return False
    try: return bool(infos) and all(ipaddress.ip_address(i[4][0].split("%")[0]).is_global for i in infos)
    except ValueError: return False

async def robots_ok(c, u):
    p = urlparse(u); ru = f"{p.scheme}://{p.netloc}/robots.txt"
    try:
        if not await asyncio.to_thread(safe_url, ru): return True
        r = await c.get(ru)
        if r.status_code != 200: return True
        rp = RobotFileParser(); rp.parse(r.text.splitlines())
        return rp.can_fetch(BOT, u)
    except Exception: return True

async def fetch(u):
    """-> (final_url, html) or None. Redirects followed by hand so every hop is checked."""
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False, headers=H, transport=TRANSPORT) as c:
            for _ in range(4):
                if not await asyncio.to_thread(safe_url, u): return None
                if not await robots_ok(c, u): return None
                async with c.stream("GET", u) as r:
                    if r.is_redirect and r.headers.get("location"): u = urljoin(u, r.headers["location"]); continue
                    if r.status_code != 200 or "html" not in r.headers.get("content-type", "text/html"): return None
                    buf = bytearray()
                    async for ch in r.aiter_bytes():
                        buf += ch
                        if len(buf) > MAX: break
                    return u, bytes(buf).decode(r.encoding or "utf-8", errors="ignore")
    except Exception: return None
    return None

def extract(html, url):
    """-> dict(title, author, date, host, text)."""
    out = {"title": None, "author": None, "date": None, "host": urlparse(url).hostname or "", "text": ""}
    try:
        import trafilatura
        j = json.loads(trafilatura.extract(html, url=url, output_format="json", with_metadata=True) or "{}")
        out.update(title=j.get("title"), author=j.get("author"), date=j.get("date"), text=j.get("text") or "")
        if not out["text"]: out["text"] = trafilatura.extract(html) or ""
    except Exception: pass
    return out
