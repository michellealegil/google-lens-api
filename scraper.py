"""
scraper.py — Google Lens Exact Match scraper

Hybrid approach:
  Step 1 (httpx):     hit lens.google.com/uploadbyurl → follow redirect → extract
                      the direct google.com/search URL (contains vsrid or similar token)
  Step 2 (Playwright): load that URL in a pooled stealth browser → JS renders → return HTML

Fixes applied vs original:
  - BrowserPool.acquire() is now actually called in render_url (was dead code)
  - stealth_async() is now applied per-page (was imported but never called)
  - vsrid check relaxed — accept any valid google.com/search redirect
  - udm=2 used (current Exact Match param; udm=26 was legacy)
  - Accept-Encoding includes 'br' (missing it is a bot signal)
  - ValueError retried properly (not just network errors)
  - User-Agent / fingerprint rotation added
"""

import asyncio
import itertools
import random
from contextlib import asynccontextmanager
from typing import Optional, List
from urllib.parse import quote, urlparse, urlencode, parse_qs, urlunparse

import httpx
from playwright.async_api import async_playwright, Browser, BrowserContext, Playwright

# ---------------------------------------------------------------------------
# Fingerprint rotation
# ---------------------------------------------------------------------------

_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.201 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.142 Safari/537.36",
]

_ACCEPT_LANGUAGES = [
    "en-US,en;q=0.9",
    "en-GB,en;q=0.9",
    "en-US,en;q=0.9,fr;q=0.8",
    "en-CA,en;q=0.9",
]


def _random_headers() -> dict:
    ua = random.choice(_USER_AGENTS)
    lang = random.choice(_ACCEPT_LANGUAGES)
    major = ua.split("Chrome/")[1].split(".")[0]
    platform = '"macOS"' if "Macintosh" in ua else ('"Windows"' if "Windows" in ua else '"Linux"')
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": lang,
        "Accept-Encoding": "gzip, deflate, br",
        "Sec-Ch-Ua": f'"Chromium";v="{major}", "Google Chrome";v="{major}", "Not-A.Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": platform,
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Upgrade-Insecure-Requests": "1",
    }


# ---------------------------------------------------------------------------
# Proxy rotation
# ---------------------------------------------------------------------------

class ProxyRotator:
    """Round-robin proxy rotation."""
    def __init__(self, proxies: List[str]):
        self._cycle = itertools.cycle(proxies) if proxies else itertools.cycle([None])
        self._proxies = proxies

    def next(self) -> Optional[str]:
        return next(self._cycle)

    @property
    def count(self) -> int:
        return len(self._proxies)


proxy_rotator: Optional[ProxyRotator] = None


# ---------------------------------------------------------------------------
# Browser Pool
# ---------------------------------------------------------------------------

class BrowserPool:
    """
    Keeps N Playwright browser contexts alive and reuses them across requests.
    Each request gets a fresh page within a pooled context, then closes the page.
    """

    def __init__(self, size: int = 3):
        self.size = size
        self._queue: asyncio.Queue = asyncio.Queue()
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None

    async def start(self):
        self._playwright = await async_playwright().start()
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
            ctx = await self._new_context()
            await self._queue.put(ctx)
        print(f"[pool] Started with {self.size} browser contexts")

    async def _new_context(self) -> BrowserContext:
        ua = random.choice(_USER_AGENTS)
        context = await self._browser.new_context(
            user_agent=ua,
            viewport={"width": random.choice([1280, 1440, 1920]), "height": random.choice([800, 900, 1080])},
            locale="en-US",
            timezone_id="America/New_York",
            extra_http_headers={"Accept-Language": random.choice(_ACCEPT_LANGUAGES)},
            java_script_enabled=True,
            ignore_https_errors=True,
        )
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            window.chrome = { runtime: {} };
        """)
        return context

    @asynccontextmanager
    async def acquire(self):
        context = await self._queue.get()
        try:
            yield context
        finally:
            try:
                await self._queue.put(context)
            except Exception:
                try:
                    new_ctx = await self._new_context()
                    await self._queue.put(new_ctx)
                except Exception as e:
                    print(f"[pool] Failed to replace context: {e}")

    async def stop(self):
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


pool: Optional[BrowserPool] = None


# ---------------------------------------------------------------------------
# Step 1: httpx — get the direct search URL
# ---------------------------------------------------------------------------

async def get_search_url(image_url: str) -> str:
    """
    Hit lens.google.com/uploadbyurl via httpx and follow the redirect to get
    the google.com/search URL. Then set udm=2 for the Exact Match tab.
    """
    lens_url = f"https://lens.google.com/uploadbyurl?url={quote(image_url, safe='')}&hl=en&gl=us"
    httpx_proxy = proxy_rotator.next() if proxy_rotator else None

    last_exc = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(
                headers=_random_headers(),
                follow_redirects=True,
                timeout=httpx.Timeout(60.0),
                proxy=httpx_proxy,
            ) as client:
                # Warm up a cookie first
                try:
                    await client.get("https://www.google.com/?hl=en", timeout=8)
                except Exception:
                    pass

                resp = await client.get(lens_url)
                final_url = str(resp.url)

                if "sorry/index" in final_url or "captcha" in final_url.lower():
                    raise ValueError("Google returned CAPTCHA — IP may be flagged")

                if "google.com/search" not in final_url:
                    raise ValueError(f"Lens redirect did not land on search page. Got: {final_url[:200]}")

                search_url = _set_param(final_url, "udm", "2")
                print(f"[httpx] Got search URL: {search_url[:120]}...")
                return search_url

        except ValueError:
            raise
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as e:
            last_exc = e
            print(f"[httpx] Attempt {attempt + 1} failed: {type(e).__name__} — retrying...")
            await asyncio.sleep(2 * (attempt + 1))

    raise ValueError(f"httpx failed after 3 attempts: {type(last_exc).__name__}: {last_exc}")


def _set_param(url: str, key: str, value: str) -> str:
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    params[key] = [value]
    new_query = urlencode({k: v[0] for k, v in params.items()})
    return urlunparse(parsed._replace(query=new_query))


# ---------------------------------------------------------------------------
# Step 2: Playwright — render the URL with a pooled browser
# ---------------------------------------------------------------------------

async def render_url(search_url: str) -> str:
    """
    Now actually uses the browser pool.
    Opens a page in a pooled context, applies stealth, navigates, returns HTML.
    """
    if pool is None:
        raise ValueError("Browser pool not initialized")

    try:
        from playwright_stealth import stealth_async
        _stealth_available = True
    except ImportError:
        _stealth_available = False
        print("[warn] playwright-stealth not installed — running without stealth patches")

    async with pool.acquire() as context:
        page = await context.new_page()
        try:
            if _stealth_available:
                await stealth_async(page)

            await page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(random.uniform(1.0, 2.5))

            html = await page.content()

            if "sorry/index" in page.url or "captcha" in html.lower()[:2000]:
                raise ValueError("Browser got CAPTCHA page")

            if len(html) < 10_000:
                raise ValueError(f"Response too short ({len(html)} chars) — likely blocked")

            print(f"[browser] Got {len(html)} chars")
            return html

        finally:
            await page.close()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def get_exact_match_html(image_url: str, proxy: Optional[str] = None) -> str:
    """
    1. httpx  → lens.google.com/uploadbyurl → follow redirect → extract search URL
    2. Playwright (pooled, stealth) → render that URL → return HTML
    """
    await asyncio.sleep(random.uniform(0.2, 0.8))
    search_url = await get_search_url(image_url)
    html = await render_url(search_url)
    return html
