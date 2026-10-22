# Fake News Detector (v1.0)

Paste a claim, headline, forwarded WhatsApp message or news link -> get a verdict, a plain explanation, a "what to do next" line and links.
India-first. English, Hindi and Hinglish. Free tiers only. No login.

## Features
| Area | What it does |
|---|---|
| Verdict | TRUE / FALSE / MISLEADING / PARTLY_TRUE / UNVERIFIED + confidence (high/medium/low) + 2-4 line explanation (same language as the message) |
| LLM-first | The model decides. "No article found" never turns a claim into rumour/false. Static facts (science, history, geography) answered from knowledge, current facts via Google Search |
| Provider chain | 1) Gemini + Google Search grounding -> 2) plain Gemini -> 3) Groq (`openai/gpt-oss-120b`, then 20b, then qwen). "Server busy" only if ALL fail. Quota cool-down so a failing provider isn't hammered |
| Honest labels | "AI-checked using Google Search" / "AI general knowledge (no live source found)" / "AI general knowledge (live search unavailable)" + which provider answered |
| Stale-knowledge guard | Without live search (fallback providers) a current-fact claim (office-holders, "current/latest", recent events) is NEVER answered FALSE from memory: it becomes UNVERIFIED with a clear "live search unavailable, verify with the links" message (`downgraded_from` shows what the model first said). Fallback providers also get the latest GNews / Fact Check headlines as evidence; TRUE is kept (confidence max medium) only when those headlines support it |
| WhatsApp-forward checks | NOT-GIVEN rule (never invents source/date), per-claim verdicts (a fake claim can't hide in a true message), no-source / single-outlet / old-news-as-new / no-date warnings, "do this next" advice line |
| Links | up to 5: Google grounding sources + Google Fact Check + GNews (links only, never decide the verdict) + "Search on Google" link, always |
| Input | text (<= 2000 chars) or news URL (safe fetch: public IPs only, robots.txt respected, 1 MB cap; title/date/host fed to the checker). Opinion/prediction -> UNVERIFIED with a clear reason |
| Cache | On disk (SQLite), survives restarts. Grounded answers 24h, fallback answers 1h. Identical simultaneous claims share ONE provider call |
| Share | Result page link (`/?r=<id>`, 30 days), Copy report, WhatsApp share button, feedback buttons |
| API | `POST /api/v1/verify`, `GET /api/v1/result/{id}`, `POST /api/v1/feedback`, `GET /health`, docs at `/docs`. Optional API keys, per-IP / per-key rate limits |
| Safety | SSRF-proof URL fetch, keys never logged, claim text treated as data (prompt-injection safe), security headers, DB failure never breaks a request |
| Tools | `tools/preflight.py` (all providers), `tools/diagnose_gemini.py` (why 429), `tools/try_claims.py`, `tools/check_retrieval.py`, `eval/run_eval.py` (60-claim starter set, accuracy + confusion matrix) |

Not included on purpose: user accounts, deepfake/video checks, browser extension, bots (v2 in the SRD). "First reported by" is shown only as *earliest report among the links found*; "recorded by" is always "not identified" (never guessed).

## Run locally (Windows / Mac / Linux, Python 3.12 or 3.13)
```
python -m venv .venv
.venv\Scripts\activate            # Mac/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
copy .env.example .env            # Mac/Linux: cp .env.example .env   -> then fill the keys
python tools/preflight.py         # are my keys/quotas OK?
uvicorn app.main:app --reload     # open http://localhost:8000
python -m pytest -q               # 44 tests, no internet needed
```

## Keys (`.env`) - all free tiers
| Var | Where to get it | Needed? |
|---|---|---|
| `GEMINI_API_KEY` | aistudio.google.com | at least ONE of Gemini / Groq |
| `GROQ_API_KEY` | console.groq.com | at least ONE of Gemini / Groq |
| `GNEWS_KEY` | gnews.io (100 req/day free) | optional (extra links) |
| `GOOGLE_FACTCHECK_KEY` | Google Cloud console -> enable "Fact Check Tools API" | optional (extra links) |
| `API_KEYS` | any strings, comma separated | optional (for API clients; website needs none) |
| `DB_PATH` | e.g. `/data/data.db` on a persistent disk | optional |
| `ALLOW_DEBUG=1` | - | optional: lets `"debug": true` return the retrieval trace |
| `CORS_ORIGINS` | e.g. `https://myfront.vercel.app` | only if the website is hosted elsewhere |
| `TRUST_PROXY` | `1` (default) behind Render/Railway/Nginx | read real visitor IP from X-Forwarded-For |

Models, cache hours, rate limits: `config.yaml`. Trusted-source registry: `sources.yaml`.

## API
```
POST /api/v1/verify   {"text": "...", "url": "https://..."}      (one of them)  header x-api-key optional
-> {claim, verdict, confidence, explanation, advice, claims[], warnings[], basis_label, provider, sources[],
    google_search_url, first_reported, recorded_by, input_meta, disclaimer, request_id, created_at}
GET  /api/v1/result/{request_id}      shared result
POST /api/v1/feedback {"request_id": "...", "is_correct": false, "comment": "..."}
```
Errors: 422 bad input, 401 bad api key, 429 too many requests (Retry-After), 503 "Server busy" (every provider failed).

## Troubleshooting
* **Always "Server busy"** -> run `python tools/preflight.py`; it shows which provider fails and why. `python tools/diagnose_gemini.py` explains Gemini 429s (e.g. "limit: 0" = no free quota for that model/tool; Google Search grounding may not be free on 3.x models - then the plain-Gemini/Groq fallback answers).
* **"live search unavailable" label** -> grounded call failed; answer came from the fallback. Normal while quota is exhausted.
* **No links** -> verdict + explanation are still shown, plus the "Search on Google" link. Check GNEWS_KEY / GOOGLE_FACTCHECK_KEY with preflight.
* **Cache/feedback/shared links vanish after redeploy** -> your host has no persistent disk; set `DB_PATH` to a mounted volume (see DEPLOY.md).
* Model names change over time (Groq already retired its llama models). Edit `config.yaml` -> `model`, `plain_model`, `groq_models`.
