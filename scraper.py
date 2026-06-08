"""
scraper.py — Google Lens Exact Match scraper

Hybrid approach:
  Step 1 (httpx):     hit lens.google.com/uploadbyurl → follow redirect → extract
                      the direct search URL with vsrid + udm=26
  Step 2 (Playwright): load that URL directly in a pooled browser context →
                      JavaScript renders → return full HTML

Why hybrid:
  - httpx gets us to the right URL without navigating the UI (reverse-engineering)
  - Playwright renders the JS that loads actual Exact Match results
  - Browser pool means we reuse browsers across requests — faster + concurrent
"""

import os
import asyncio
import random
import re
import itertools
from contextlib import asynccontextmanager
from urllib.parse import quote, urlparse, urlencode, parse_qs, urlunparse
from typing import Optional, List

import httpx
from playwright.async_api import async_playwright, Browser, BrowserContext, Playwright

# ---------------------------------------------------------------------------
# Proxy rotation
# ---------------------------------------------------------------------------

class ProxyRotator:
    """Round-robin proxy rotation. Add proxies as strings: 'http://user:pass@host:port'"""
    def __init__(self, proxies: List[str]):
        self._cycle = itertools.cycle(proxies) if proxies else itertools.cycle([None])
        self._proxies = proxies

    def next(self) -> Optional[str]:
        return next(self._cycle)

    @property
    def count(self) -> int:
        return len(self._proxies)

# Global rotator — set via PROXY_LIST env var or add manually
proxy_rotator: Optional[ProxyRotator] = None

# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------

CHROME_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}

# ---------------------------------------------------------------------------
# Browser Pool
# ---------------------------------------------------------------------------

class BrowserPool:
    """
    Keeps N Playwright browser contexts alive and reuses them across requests.
    Each context gets its own page per request, then the page is closed.
    """

    def __init__(self, size: int = 3, proxy: Optional[str] = None):
        self.size = size
        self.proxy = proxy
        self._queue: asyncio.Queue = asyncio.Queue()
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None

    async def start(self):
        """Launch browser and fill the pool with contexts."""
        self._playwright = await async_playwright().start()

        proxy_config = {"server": self.proxy} if self.proxy else None

        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--ignore-certificate-errors",
            ],
        )

        for _ in range(self.size):
            context = await self._new_context()
            await self._queue.put(context)

        print(f"[pool] Started with {self.size} browser contexts")

    async def _new_context(self) -> BrowserContext:
        # Build proxy config at context level (more stable than browser-level)
        ctx_proxy = None
        if self.proxy:
            from urllib.parse import urlparse as _urlparse
            _p = _urlparse(self.proxy)
            ctx_proxy = {
                "server": f"{_p.scheme}://{_p.hostname}:{_p.port}",
                "username": _p.username or "",
                "password": _p.password or "",
            }

        context = await self._browser.new_context(
            user_agent=CHROME_HEADERS["User-Agent"],
            viewport={"width": 1280, "height": 800},
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            java_script_enabled=True,
            proxy=ctx_proxy,
            ignore_https_errors=True,
        )
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            window.chrome = { runtime: {} };
        """)

        # Apply playwright-stealth if available
        try:
            from playwright_stealth import stealth_async
            # We'll apply per-page, not per-context
        except ImportError:
            pass

        return context

    @asynccontextmanager
    async def acquire(self):
        """Get a context from the pool, yield it, return it when done."""
        context = await self._queue.get()
        try:
            yield context
        finally:
            # Return context to pool (or replace if broken)
            try:
                await self._queue.put(context)
            except Exception:
                # Context broken — create a fresh one
                try:
                    new_ctx = await self._new_context()
                    await self._queue.put(new_ctx)
                except Exception as e:
                    print(f"[pool] Failed to replace context: {e}")

    async def stop(self):
        """Drain pool and close browser."""
        while not self._queue.empty():
            try:
                ctx = self._queue.get_nowait()
                await ctx.close()
            except Exception:
                pass
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        print("[pool] Stopped")


# Global pool instance (initialized by FastAPI lifespan)
pool: Optional[BrowserPool] = None


# ---------------------------------------------------------------------------
# Step 1: httpx — get the direct search URL with vsrid
# ---------------------------------------------------------------------------

async def get_search_url(image_url: str) -> str:
    """
    Navigate to lens.google.com/uploadbyurl in a real Playwright browser,
    follow the redirect to get the google.com/search URL with vsrid + udm=26.
    Uses the browser pool — much better CAPTCHA resistance than raw httpx.
    """
    if pool is None:
        raise RuntimeError("Browser pool not initialized")

    lens_url = f"https://lens.google.com/uploadbyurl?url={quote(image_url, safe='')}"

    try:
        from playwright_stealth import stealth_async
        USE_STEALTH = True
    except ImportError:
        USE_STEALTH = False

    async with pool.acquire() as context:
        page = await context.new_page()
        try:
            if USE_STEALTH:
                await stealth_async(page)

            await page.goto(lens_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(1.0, 2.0))

            final_url = page.url
            print(f"[playwright] Redirected to: {final_url[:120]}...")

            if "sorry/index" in final_url:
                raise ValueError("Google returned CAPTCHA page for Lens request — IP may be flagged")

            if "google.com/search" not in final_url or "vsrid" not in final_url:
                raise ValueError(f"Lens redirect did not land on search page. Got: {final_url[:200]}")

            search_url = _set_param(final_url, "udm", "26")
            print(f"[playwright] Got search URL: {search_url[:120]}...")
            return search_url

        finally:
            await page.close()


def _set_param(url: str, key: str, value: str) -> str:
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    params[key] = [value]
    new_query = urlencode({k: v[0] for k, v in params.items()})
    return urlunparse(parsed._replace(query=new_query))


# ---------------------------------------------------------------------------
# Step 2: Playwright — render the URL and return HTML
# ---------------------------------------------------------------------------

async def render_url(search_url: str) -> str:
    """
    Load the search URL in a pooled browser context.
    Waits for the page to fully render and returns the HTML.
    """
    if pool is None:
        raise RuntimeError("Browser pool not initialized")

    try:
        from playwright_stealth import stealth_async
        USE_STEALTH = True
    except ImportError:
        USE_STEALTH = False

    async with pool.acquire() as context:
        page = await context.new_page()
        try:
            if USE_STEALTH:
                await stealth_async(page)

            await page.goto(
                search_url,
                wait_until="networkidle",
                timeout=30000,
            )
            await asyncio.sleep(random.uniform(1.0, 2.0))

            html = await page.content()
            print(f"[playwright] Got {len(html)} chars from {page.url[:80]}")

            if len(html) < 10_000:
                raise ValueError(f"Page too short ({len(html)} chars) — likely blocked")

            return html

        finally:
            await page.close()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def get_exact_match_html(image_url: str, proxy: Optional[str] = None) -> str:
    """
    Full Playwright flow:
      1. Playwright → navigate to lens.google.com/uploadbyurl → follow redirect → get vsrid URL
      2. Playwright → render that URL with JS → return HTML
    Both steps use the browser pool for CAPTCHA resistance.
    """
    await asyncio.sleep(random.uniform(0.2, 0.8))

    # Step 1: get search URL via browser (avoids CAPTCHA vs raw httpx)
    search_url = await get_search_url(image_url)

    # Step 2: render via pooled browser
    html = await render_url(search_url)
    return html
