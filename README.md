# Fake News Detector (MVP backend)
Setup: `pip install -r requirements.txt && cp .env.example .env` (fill keys)
Run: `uvicorn app.main:app --reload` -> POST /api/v1/verify
Test: `pytest`
Docker: `docker compose up --build`
Edit `sources.yaml` (registry) and `config.yaml` (weights) - no code change needed.
Free tiers only: Google Fact Check API, GNews (100 req/day), RSS. UI at / (static, no login).
TODO: eval set, recorder detection, deploy.
Feedback: POST /api/v1/feedback {request_id,is_correct,comment} -> SQLite (data.db). Rate limit in config.yaml.

## Eval
Starter set: `eval/claims.csv` (60 claims: 20 true, 20 false/misleading, 20 synthetic rumours).
All rows are `status=unverified` - check each one, add real PIB/Alt News URL, then set `status=verified`.
Run: `python eval/run_eval.py --limit 20` (resumable; repeat daily for free quota), `--report` = just print metrics.
