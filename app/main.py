from dotenv import load_dotenv; load_dotenv()  # read .env
import logging, os, re
from contextlib import asynccontextmanager
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from . import db, ratelimit
from .llm import Busy, LLMError
from .pipeline import verify
from .registry import CFG

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("fnd.api")
KEYS = set(filter(None, (k.strip() for k in os.getenv("API_KEYS", "").split(","))))  # optional keys for API clients

@asynccontextmanager
async def lifespan(_app):
    log.info("providers configured: gemini=%s groq=%s gnews=%s factcheck=%s | api_keys=%d",
             bool(os.getenv("GEMINI_API_KEY")), bool(os.getenv("GROQ_API_KEY")), bool(os.getenv("GNEWS_KEY")),
             bool(os.getenv("GOOGLE_FACTCHECK_KEY")), len(KEYS))
    yield

app = FastAPI(title="Fake News Detector", version="1.0", lifespan=lifespan)

if os.getenv("CORS_ORIGINS"):  # only needed if the website is hosted on a different domain than this API
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in os.environ["CORS_ORIGINS"].split(",")],
                       allow_methods=["GET", "POST"], allow_headers=["*"])

@app.middleware("http")
async def sec_headers(req, call_next):
    r = await call_next(req)
    r.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"})
    return r

class Req(BaseModel):
    text: str | None = Field(None, max_length=2000)
    url: str | None = Field(None, max_length=2000)
    language: str = "auto"   # model auto-detects English / Hindi / Hinglish
    debug: bool = False      # honoured only when ALLOW_DEBUG=1

class FB(BaseModel):
    request_id: str = Field(max_length=64)
    is_correct: bool
    comment: str | None = Field(None, max_length=500)

def client_ip(req: Request):
    if os.getenv("TRUST_PROXY", "1") == "1":  # behind Render/Railway/Nginx the real IP is in X-Forwarded-For
        xf = req.headers.get("x-forwarded-for")
        if xf: return xf.split(",")[0].strip()
    return req.client.host if req.client else "anon"

def guard(req: Request, key, bucket="main"):
    """Optional API key + rate limit. No key = website visitor (per IP). Wrong key = 401."""
    if key:
        if key not in KEYS: raise HTTPException(401, "bad api key")
        who, lim = f"key:{key}", CFG["rate_limit"]["key"]
    else:
        who, lim = f"{bucket}:{client_ip(req)}", CFG["rate_limit"]["view" if bucket == "view" else "public"]
    ok, wait = ratelimit.check(who, lim["per_min"], lim["per_day"])
    if not ok:
        raise HTTPException(429, f"Too many requests. Try again in {wait}s.", headers={"Retry-After": str(wait)})

@app.post("/api/v1/verify")
async def api_verify(r: Req, req: Request, x_api_key: str | None = Header(None)):
    guard(req, x_api_key)
    if not ((r.text or "").strip() or (r.url or "").strip()):
        raise HTTPException(422, "Send a claim (text) or a news URL.")
    try:
        d = await verify(r.text, r.url, debug=r.debug and os.getenv("ALLOW_DEBUG") == "1")
    except Busy as e:  # EVERY provider failed
        log.error("all providers failed: %s", e.reasons)
        raise HTTPException(503, "Server busy, please try again in a minute.", headers={"Retry-After": "60"})
    except LLMError as e:
        log.error("LLM failed: %s", e)
        raise HTTPException(502, "Could not get a verdict right now. Please try again.")
    if "error" in d: raise HTTPException(422, d["error"])
    db.save_ver(d["request_id"], d)
    db.save_result(d["request_id"], {k: v for k, v in d.items() if k != "debug"})  # powers the share link
    return d

@app.get("/api/v1/result/{rid}")
def api_result(rid: str, req: Request):
    guard(req, None, "view")
    d = db.get_result(rid) if re.fullmatch(r"[0-9a-f-]{36}", rid) else None
    if not d: raise HTTPException(404, "Result not found (links expire after %d days)." % CFG.get("result_days", 30))
    return d

@app.post("/api/v1/feedback")
def api_feedback(f: FB, req: Request, x_api_key: str | None = Header(None)):
    guard(req, x_api_key)
    if not db.has_ver(f.request_id): raise HTTPException(404, "unknown request_id")
    db.save_fb(f.request_id, f.is_correct, f.comment)
    return {"ok": True}

@app.get("/health")
def health():
    return {"ok": True, "providers": {"gemini": bool(os.getenv("GEMINI_API_KEY")), "groq": bool(os.getenv("GROQ_API_KEY")),
            "gnews": bool(os.getenv("GNEWS_KEY")), "factcheck": bool(os.getenv("GOOGLE_FACTCHECK_KEY"))}}

app.mount("/", StaticFiles(directory=str(db.Path(__file__).resolve().parent.parent / "static"), html=True))  # website (/?r=<id> = shared result)
