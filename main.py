"""
main.py — FastAPI app for Google Lens Exact Match API

Endpoint:
  GET /google-lens?imageUrl={image_url}

Returns the raw HTML of the Google Lens Exact Match results page.
"""

import os
import sys
import subprocess
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

import scraper
from scraper import BrowserPool, get_exact_match_html

POOL_SIZE = int(os.getenv("POOL_SIZE", "3"))

_proxy_list_raw = os.getenv("PROXY_LIST", "")
PROXY_LIST = [p.strip() for p in _proxy_list_raw.split(",") if p.strip()]


async def _initialize():
    """Install Chromium and start browser pool in the background."""
    print("[startup] Installing Playwright Chromium...")
    await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True
        )
    )
    print("[startup] Chromium ready")

    if PROXY_LIST:
        scraper.proxy_rotator = scraper.ProxyRotator(PROXY_LIST)
        print(f"[proxies] Loaded {len(PROXY_LIST)} proxies")
    else:
        print("[proxies] No proxies configured — running without")

    scraper.pool = BrowserPool(size=POOL_SIZE)
    await scraper.pool.start()
    print("[startup] Ready")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Kick off init as background task — port binds immediately, no timeout
    asyncio.create_task(_initialize())
    yield
    if scraper.pool:
        await scraper.pool.stop()


app = FastAPI(
    title="Google Lens Exact Match API",
    description="Returns the Exact Match HTML for a given image URL via Google Lens.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/google-lens", response_class=HTMLResponse)
async def google_lens(
    imageUrl: str = Query(..., description="Image URL to search on Google Lens")
):
    if not imageUrl.startswith("http"):
        raise HTTPException(status_code=400, detail="imageUrl must be a valid HTTP/HTTPS URL")

    if scraper.pool is None:
        raise HTTPException(status_code=503, detail="Service is warming up — try again in 60 seconds")

    try:
        html = await get_exact_match_html(imageUrl)
        return HTMLResponse(content=html, status_code=200)

    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))

    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Request timed out")

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")


@app.get("/health")
async def health():
    ready = scraper.pool is not None
    return JSONResponse({"status": "ready" if ready else "warming_up", "pool_size": scraper.pool.size if ready else 0})


@app.get("/")
async def root():
    return JSONResponse({
        "service": "Google Lens Exact Match API",
        "usage": "GET /google-lens?imageUrl={your_image_url}",
        "example": "GET /google-lens?imageUrl=https://i.ebayimg.com/00/s/MTYwMFgxNjAw/z/BVcAAOSwS-9m4zOb/$_57.JPG",
        "pool_size": POOL_SIZE,
    })
