from dotenv import load_dotenv; load_dotenv()  # read .env
import logging, os
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from . import db, ratelimit
from .pipeline import verify
from .registry import CFG

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
app = FastAPI(title="Fake News Detector")
KEYS = set(filter(None, os.getenv("API_KEYS", "").split(",")))

class Req(BaseModel):
    text: str | None = Field(None, max_length=2000)
    url: str | None = None
    language: str = "auto"
    debug: bool = False  # add retrieval funnel to the response

class FB(BaseModel):
    request_id: str
    is_correct: bool
    comment: str | None = Field(None, max_length=500)

def guard(req: Request, key):
    """API key check (if configured) + rate limit per key/IP."""
    if KEYS and key not in KEYS:
        raise HTTPException(401, "bad api key")
    who = key or (req.client.host if req.client else "anon")
    ok, wait = ratelimit.check(who, CFG["rate_limit"]["per_min"], CFG["rate_limit"]["per_day"])
    if not ok:
        raise HTTPException(429, f"Too many requests. Try again in {wait}s.", headers={"Retry-After": str(wait)})

@app.post("/api/v1/verify")
async def api_verify(r: Req, req: Request, x_api_key: str | None = Header(None)):
    guard(req, x_api_key)
    if not (r.text or r.url):
        raise HTTPException(422, "send text or url")
    d = await verify(r.text, r.url, r.debug)
    if "error" not in d: db.save_ver(d["request_id"], d)
    return d

@app.post("/api/v1/feedback")
def api_feedback(f: FB, req: Request, x_api_key: str | None = Header(None)):
    guard(req, x_api_key)
    if not db.has_ver(f.request_id):
        raise HTTPException(404, "unknown request_id")
    db.save_fb(f.request_id, f.is_correct, f.comment)
    return {"ok": True}

@app.get("/health")
def health(): return {"ok": True}

app.mount("/", StaticFiles(directory="static", html=True))  # simple UI
