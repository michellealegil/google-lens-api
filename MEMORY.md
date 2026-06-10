# MrScraper Coding Challenge — Session Memory

## Project
- **Role**: Data Acquisition Engineer at MrScraper
- **Challenge**: Build a FastAPI that accepts an image URL, runs a Google Lens visual search, navigates to the Exact Match results page, and returns the full HTML
- **Repo**: https://github.com/michellealegil/google-lens-api
- **Live URL**: https://google-lens-api-4qrj.onrender.com
- **Endpoint**: `GET /google-lens?imageUrl={image_url}`

## Architecture
Hybrid two-step approach:
1. **httpx** — hits `lens.google.com/uploadbyurl` → follows redirect → extracts direct search URL with `vsrid + udm=26` (Exact Match tab)
2. **Playwright** — renders that URL in a pooled browser context → returns full HTML

Why hybrid: httpx is fast for URL extraction (no browser needed); Playwright handles JS rendering for actual results.

## Key Files
- `main.py` — FastAPI app, lifespan manages browser pool startup/shutdown
- `scraper.py` — All Playwright + httpx logic, browser pool, proxy rotation
- `render.yaml` — Render config (note: doesn't override existing service settings)
- `requirements.txt` — Dependencies

## Deployment: Render (Free tier)
- Connected to GitHub repo, auto-deploys on push
- **Instance**: Free (512MB RAM, spins down on inactivity — 50s+ cold start)
- **Env vars set**:
  - `POOL_SIZE=1` (reduces memory usage on free tier)
  - `HTTPX_PROXY=http://michellealegilgmailcom-country-us:JeazETiS@proxy.mrscraper.com:10000`

## Proxy Situation (important context)
- **MrScraper provides HTTP proxies** at `proxy.mrscraper.com:10000`
- **Problem**: Their proxy does SSL inspection → crashes Playwright/Chromium (`Connection closed while reading from driver`)
- **No SOCKS5 available** from MrScraper (only HTTP on port 10000)
- **Solution**: Use MrScraper proxy for httpx step ONLY (via `HTTPX_PROXY` env var), keep Playwright proxy-free on Render's IP
- **Why this works**: httpx handles the proxy SSL fine; Playwright runs on Render's clean IP

## Issues Solved
1. **Chromium not found at runtime** — Render's build/runtime envs are different. Fixed by installing Chromium inside FastAPI lifespan (after port binds), using `asyncio.run_in_executor` so it's non-blocking
2. **`render.yaml` didn't override existing service** — render.yaml only applies to NEW services. Workaround: code-level fix (lifespan install)
3. **CAPTCHA on httpx step** — Render/cloud IPs flagged by Google. Fixed by routing httpx through MrScraper's proxy via `HTTPX_PROXY` env var
4. **Port not binding** — Original fix put `subprocess.run(playwright install)` at module level, blocking uvicorn. Moved to lifespan so port binds first
5. **Home IP burned** — From local testing; Render gives fresh IPs

## Current Status (as of June 7, 2026)
- ✅ Code works (local testing showed 359K chars, real Exact Match HTML)
- ✅ Deployed to Render, service is live
- ✅ Chromium installs correctly as background task (port binds immediately)
- ✅ Full Playwright flow — both steps use browser pool (no httpx), better CAPTCHA resistance
- ❌ Render's IP is burned from testing — Google CAPTCHA's all requests from it
- ⏳ **Next: delete + recreate Render service to get a fresh IP, then retest**

## Test Command
```bash
curl "https://google-lens-api-4qrj.onrender.com/google-lens?imageUrl=https://i.ebayimg.com/00/s/MTYwMFgxNjAw/z/BVcAAOSwS-9m4zOb/%24_57.JPG"
```
Expected: large HTML response (~300K+ chars)

## GitHub
- Username: `michellealegil`
- Token: stored locally only — do not commit

## Next Steps (when returning)
1. Confirm latest deploy worked (HTTPX_PROXY fix)
2. If still CAPTCHA → may need to upgrade Render instance or try different approach
3. Submit to MrScraper once endpoint returns real HTML
