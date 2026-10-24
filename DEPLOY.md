# DEPLOY.md - changes live kaise honge (step by step)

Ye app ek hi service hai: FastAPI backend + website (`static/`). Jab bhi tum GitHub pe push karte ho, Render / Railway jaise hosts khud naya version live kar dete hain.

## 0. Pehle ek baar (zaroori)
1. Jo keys chat mein paste hui thi (Gemini, Google Fact Check, GNews) - **naye generate karke purani delete kar do**.
2. `.env` file kabhi GitHub pe push nahi hoti (`.gitignore` mein already hai). Keys sirf host ke "Environment Variables" mein daalni hain.

## 1. Laptop pe test karo (2 minute)
```
pip install -r requirements-dev.txt
python tools/preflight.py          # READY: ... dikhna chahiye
python -m pytest -q
uvicorn app.main:app --reload      # http://localhost:8000 pe 4 claims check karo
```

## 2. Naya version GitHub pe daalo
1. Is zip ko extract karo. Apne project ke repo folder mein **saari files copy-paste (replace)** kar do. `.git` folder, aur apni `.env` ko haath mat lagao.
2. Terminal (repo folder mein):
```
git status
git add .
git commit -m "v1.0: provider fallback, disk cache, share links, WhatsApp checks"
git push
```
3. Host ka auto-deploy chalu hota hai. Dashboard -> Deploys/Logs mein "Live" aane tak ruko (2-5 min).
   Agar auto-deploy off hai: Render -> "Manual Deploy -> Deploy latest commit"; Railway -> "Deploy".

## 3. Host pe settings (ek baar)
| Setting | Value |
|---|---|
| Build command | `pip install -r requirements.txt` |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'` |
| Health check path | `/health` |
| Python | 3.12 (file `.python-version` / env `PYTHON_VERSION=3.12`) |
| Env vars | `GEMINI_API_KEY`, `GROQ_API_KEY`, `GNEWS_KEY`, `GOOGLE_FACTCHECK_KEY` (+ optional `API_KEYS`, `DB_PATH`, `ALLOW_DEBUG`) |
Docker bhi chalta hai: repo mein `Dockerfile` hai (Render/Railway/Fly sab detect kar lete hain).

### Render
New -> Web Service -> GitHub repo -> upar wali settings -> Environment tab mein keys -> Create.
* Free plan pe service 15 min idle ke baad so jaati hai (pehli request slow) aur **disk persistent nahi hota**: cache, feedback aur share links har redeploy/restart pe reset ho jaate hain. App phir bhi chalta hai.
* Persistent chahiye: paid plan -> Disks -> mount path `/data` -> env `DB_PATH=/data/data.db`.

### Railway
New Project -> Deploy from GitHub repo -> Variables mein keys -> Settings -> Networking -> Generate Domain.
Persistent: Add Volume, mount `/data`, env `DB_PATH=/data/data.db`.

### Vercel
Vercel serverless hai: file system persistent nahi, rate-limit/cool-down memory har instance ka alag. Is backend ke liye Render/Railway/Fly better hain. Vercel sirf alag frontend ke liye use karo (tab backend pe env `CORS_ORIGINS=https://tumhara-frontend.vercel.app`).

## 4. Live hone ke baad check (5 checks)
1. `https://<tumhara-domain>/health` -> `{"ok":true,"providers":{...true...}}` (jo key daali wo `true` dikhni chahiye).
2. Website kholo -> "the sun rises in the east" -> TRUE.
3. "gst was launched in 2001" -> FALSE, explanation mein 1 July 2017.
4. Result ke neeche "Copy link" -> naye tab mein kholo -> wahi result dikhe.
5. Host ke Logs mein `providers configured: gemini=True ...` aur `provider=... ok` lines dikhni chahiye.
Local se bhi: `python tools/preflight.py` (wahi keys .env mein daal ke).

## 5. Kuch bigde to
* **Rollback:** Render -> Events/Deploys -> purane deploy pe "Rollback"; ya `git revert HEAD` -> `git push`.
* **"Server busy":** Logs mein `all providers failed: ...` line har provider ka reason batati hai (quota / auth / missing key).
* **Models change ho gaye (404 / model not found):** `config.yaml` mein `model`, `plain_model`, `groq_models` badlo -> push.
* **Rate limit zyada lag raha:** `config.yaml` -> `rate_limit` badlo -> push.
