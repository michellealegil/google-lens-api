"""
Test: parse kEI and other params from the 91K HTML response,
then use them to call /async/folsrch properly.

Run:
  python3 test_kei.py
"""

import asyncio
import re
import httpx
from urllib.parse import quote, urlparse, parse_qs, urlencode

HEADERS = {
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

TEST_IMAGE = "https://i.ebayimg.com/00/s/MTYwMFgxNjAw/z/BVcAAOSwS-9m4zOb/$_57.JPG"


def parse_google_globals(html: str) -> dict:
    """Extract Google's embedded JS globals from HTML."""
    found = {}

    # kEI — session/event ID (this is the 'ei' param)
    for pattern in [
        r'google\.kEI\s*=\s*["\']([^"\']+)["\']',
        r'"kEI"\s*:\s*"([^"]+)"',
        r'kEI\s*[=:]\s*["\']([^"\']+)["\']',
        r'var _G=\{[^}]*kEI:"([^"]+)"',
    ]:
        m = re.search(pattern, html)
        if m:
            found["ei"] = m.group(1)
            print(f"  ✅ kEI (ei): {found['ei']}")
            break

    # kBL — build label
    m = re.search(r'"kBL"\s*:\s*"([^"]+)"', html)
    if m:
        found["kbl"] = m.group(1)
        print(f"  ✅ kBL: {found['kbl']}")

    # sca_esv — experiment version
    m = re.search(r'sca_esv=([a-f0-9]+)', html)
    if m:
        found["sca_esv"] = m.group(1)
        print(f"  ✅ sca_esv: {found['sca_esv']}")

    # ved — click tracking param (appears in result links)
    m = re.search(r'ved=([A-Za-z0-9_\-]+)', html)
    if m:
        found["ved"] = m.group(1)
        print(f"  ✅ ved: {found['ved'][:40]}")

    # mlro — image results metadata (may be embedded)
    m = re.search(r'mlro=([A-Za-z0-9_\-]+)', html)
    if m:
        found["mlro"] = m.group(1)
        print(f"  ✅ mlro: {found['mlro'][:40]}")

    # folsrch ID (B2Jtyd-style ID used in async requests)
    m = re.search(r'_id:([A-Za-z0-9]+)', html)
    if m:
        found["folid"] = m.group(1)
        print(f"  ✅ folsrch _id: {found['folid']}")

    # Any /async/folsrch URL already embedded in the HTML
    m = re.search(r'(/async/folsrch\?[^"\'<\s]+)', html)
    if m:
        found["folsrch_url"] = m.group(1).replace("\\x26", "&").replace("\\u0026", "&")
        print(f"  ✅ Found embedded folsrch URL!")

    return found


async def main():
    async with httpx.AsyncClient(
        headers=HEADERS,
        follow_redirects=True,
        timeout=httpx.Timeout(30.0),
    ) as client:

        # Step 1: Lens → get vsrid
        print("[1] Getting vsrid from Lens...")
        lens_url = f"https://lens.google.com/uploadbyurl?url={quote(TEST_IMAGE, safe='')}"
        resp1 = await client.get(lens_url)
        search_url = str(resp1.url)

        parsed = urlparse(search_url)
        params = parse_qs(parsed.query)
        vsrid = params.get("vsrid", [None])[0]
        vsint = params.get("vsint", [None])[0]
        gsessionid = params.get("gsessionid", [None])[0]
        lsessionid = params.get("lsessionid", [None])[0]

        print(f"  vsrid: {vsrid[:50] if vsrid else '❌ NOT FOUND'}...")
        if not vsrid:
            return

        # Step 2: Get the 91K HTML and parse globals
        print("\n[2] Fetching search page and parsing globals...")

        # Build URL with udm=26
        search_params = {
            "vsrid": vsrid,
            "vsint": vsint,
            "udm": "26",
            "lns_mode": "un",
            "source": "lns.web.ukn",
            "lns_surface": "26",
            "lns_vfs": "e",
        }
        if gsessionid:
            search_params["gsessionid"] = gsessionid
        if lsessionid:
            search_params["lsessionid"] = lsessionid

        resp2 = await client.get(
            "https://www.google.com/search?" + urlencode(search_params),
            headers={**HEADERS, "Referer": "https://lens.google.com/"},
        )
        html = resp2.text
        print(f"  Got {len(html)} chars")

        # Save for inspection
        with open("search_91k.html", "w") as f:
            f.write(html)

        # Parse globals
        globals_found = parse_google_globals(html)

        # Step 3: Try folsrch with kEI
        print("\n[3] Trying /async/folsrch with kEI...")

        if "folsrch_url" in globals_found:
            # Google embedded the folsrch URL directly — use it!
            full_url = "https://www.google.com" + globals_found["folsrch_url"]
            print(f"  Using embedded URL: {full_url[:150]}")
            resp_f = await client.get(full_url, headers={**HEADERS, "Referer": "https://www.google.com/"})
        else:
            # Build it ourselves with what we have
            folsrch_params = {
                "vsrid": vsrid,
                "vsint": vsint,
                "udm": "26",
                "lns_mode": "un",
                "source": "lns.web.ukn",
                "lns_surface": "26",
                "yv": "3",
                "cs": "1",
                "async": "_fmt:madl",
                "q": "",
            }
            if "ei" in globals_found:
                folsrch_params["ei"] = globals_found["ei"]
            if "sca_esv" in globals_found:
                folsrch_params["sca_esv"] = globals_found["sca_esv"]
            if gsessionid:
                folsrch_params["gsessionid"] = gsessionid

            full_url = "https://www.google.com/async/folsrch?" + urlencode(folsrch_params)
            print(f"  URL: {full_url[:150]}")
            resp_f = await client.get(full_url, headers={**HEADERS, "Referer": "https://www.google.com/"})

        print(f"  Status: {resp_f.status_code}")
        print(f"  Size: {len(resp_f.text)} chars")
        print(f"  First 800 chars:\n{resp_f.text[:800]}")

        with open("folsrch_kei_response.html", "w") as f:
            f.write(resp_f.text)
        print(f"\n  Saved to folsrch_kei_response.html")
        print(f"  open folsrch_kei_response.html")

        # Quick content check
        has_results = any(x in resp_f.text for x in ["Exact match", "udm=26", "href=", "<a "])
        print(f"\n  Has result-like content: {has_results}")


if __name__ == "__main__":
    asyncio.run(main())
