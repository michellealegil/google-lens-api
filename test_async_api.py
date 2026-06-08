"""
Test Google's internal async result endpoints directly.

After getting vsrid from lens.google.com, try hitting:
  1. /async/folsrch  — the main results loader
  2. /async/bgasy    — background async results

Run:
  python3 test_async_api.py
"""

import asyncio
import httpx
from urllib.parse import quote, urlparse, parse_qs, urlencode, urlunparse

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


async def main():
    lens_url = f"https://lens.google.com/uploadbyurl?url={quote(TEST_IMAGE, safe='')}"

    async with httpx.AsyncClient(
        headers=HEADERS,
        follow_redirects=True,
        timeout=httpx.Timeout(30.0),
    ) as client:

        # -------------------------------------------------------
        # Step 1: Get vsrid and other params from Lens redirect
        # -------------------------------------------------------
        print(f"[1] Hitting Lens URL...")
        resp1 = await client.get(lens_url)
        search_url = str(resp1.url)
        print(f"    Redirected to: {search_url[:150]}")

        parsed = urlparse(search_url)
        params = parse_qs(parsed.query)

        vsrid = params.get("vsrid", [None])[0]
        vsint = params.get("vsint", [None])[0]
        gsessionid = params.get("gsessionid", [None])[0]
        lsessionid = params.get("lsessionid", [None])[0]

        print(f"    vsrid: {vsrid[:40] if vsrid else None}...")
        print(f"    vsint: {vsint[:40] if vsint else None}...")
        print(f"    gsessionid: {gsessionid}")

        if not vsrid:
            print("❌ No vsrid found — cannot proceed")
            return

        # -------------------------------------------------------
        # Step 2a: Try /async/folsrch
        # -------------------------------------------------------
        folsrch_params = {
            "vsrid": vsrid,
            "vsint": vsint,
            "udm": "26",
            "lns_mode": "un",
            "source": "lns.web.ukn",
            "lns_surface": "26",
            "yv": "3",
            "cs": "1",
            "async": "_fmt:jspb",
            "q": "",
        }
        if gsessionid:
            folsrch_params["gsessionid"] = gsessionid

        folsrch_url = "https://www.google.com/async/folsrch?" + urlencode(folsrch_params)
        print(f"\n[2a] Trying /async/folsrch...")
        print(f"     URL: {folsrch_url[:150]}")

        resp_folsrch = await client.get(
            folsrch_url,
            headers={**HEADERS, "Referer": "https://www.google.com/"},
        )
        print(f"     Status: {resp_folsrch.status_code}")
        print(f"     Size: {len(resp_folsrch.text)} chars")
        print(f"     First 500 chars:\n{resp_folsrch.text[:500]}\n")

        with open("folsrch_response.html", "w") as f:
            f.write(resp_folsrch.text)
        print("     Saved to folsrch_response.html")

        # -------------------------------------------------------
        # Step 2b: Try /async/bgasy
        # -------------------------------------------------------
        bgasy_params = {
            "lns_mode": "un",
            "lns_surface": "26",
            "source": "lns.web.ukn",
            "udm": "26",
            "vsint": vsint,
            "vsrid": vsrid,
            "yv": "3",
            "cs": "1",
            "async": "_fmt:jspb",
        }

        bgasy_url = "https://www.google.com/async/bgasy?" + urlencode(bgasy_params)
        print(f"[2b] Trying /async/bgasy...")
        print(f"     URL: {bgasy_url[:150]}")

        resp_bgasy = await client.get(
            bgasy_url,
            headers={**HEADERS, "Referer": "https://www.google.com/"},
        )
        print(f"     Status: {resp_bgasy.status_code}")
        print(f"     Size: {len(resp_bgasy.text)} chars")
        print(f"     First 500 chars:\n{resp_bgasy.text[:500]}\n")

        with open("bgasy_response.html", "w") as f:
            f.write(resp_bgasy.text)
        print("     Saved to bgasy_response.html")

        # -------------------------------------------------------
        # Step 2c: Try the full /search with udm=26 but with
        #          X-Requested-With header to get JSON-ish response
        # -------------------------------------------------------
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

        search_url2 = "https://www.google.com/search?" + urlencode(search_params)
        print(f"[2c] Trying /search with JS-enabled headers...")

        resp_search = await client.get(
            search_url2,
            headers={
                **HEADERS,
                "Referer": "https://lens.google.com/",
                "Sec-Fetch-Site": "cross-site",
            },
        )
        print(f"     Status: {resp_search.status_code}")
        print(f"     Size: {len(resp_search.text)} chars")

        # Check for results
        has_exact = "Exact match" in resp_search.text or "exact_match" in resp_search.text
        has_noscript = "<noscript>" in resp_search.text
        print(f"     Has 'Exact match': {has_exact}")
        print(f"     Has noscript: {has_noscript}")

        with open("search_response.html", "w") as f:
            f.write(resp_search.text)
        print("     Saved to search_response.html")

        print("\n✅ Done. Open the .html files to inspect responses.")
        print("   open folsrch_response.html bgasy_response.html search_response.html")


if __name__ == "__main__":
    asyncio.run(main())
