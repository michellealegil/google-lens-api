"""
Google Lens Network Investigator v2
Navigates directly to the Lens URL (no UI clicking needed),
captures all network requests, and saves results.

Run:
  python3 investigate.py
"""

import asyncio
import json
from urllib.parse import quote, urlparse, parse_qs
from playwright.async_api import async_playwright

TEST_IMAGE_URL = "https://i.ebayimg.com/00/s/MTYwMFgxNjAw/z/BVcAAOSwS-9m4zOb/$_57.JPG"

captured_requests = []


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()

        # Capture requests (skip binary post data gracefully)
        def on_request(request):
            try:
                post_data = request.post_data
            except Exception:
                post_data = "<binary>"
            if "google" in request.url:
                captured_requests.append({
                    "method": request.method,
                    "url": request.url,
                    "post_data": post_data,
                })
                print(f"→ {request.method} {request.url[:120]}")

        page.on("request", on_request)

        # -------------------------------------------------------
        # Step 1: Go directly to Lens with the image URL
        # -------------------------------------------------------
        lens_url = f"https://lens.google.com/uploadbyurl?url={quote(TEST_IMAGE_URL, safe='')}"
        print(f"\n[1] Navigating to:\n    {lens_url}\n")
        await page.goto(lens_url, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)

        print(f"\n    Current URL: {page.url}")

        # -------------------------------------------------------
        # Step 2: Look for Exact Match tab
        # -------------------------------------------------------
        print("\n[2] Looking for 'Exact match' tab...")

        # Print all visible links/tabs to help us find the right selector
        links = await page.eval_on_selector_all("a", "els => els.map(e => ({text: e.innerText.trim(), href: e.href}))")
        print("\n    All links on page:")
        for link in links:
            if link["text"]:
                print(f"      '{link['text']}' -> {link['href'][:100]}")

        # Try to find and click Exact Match
        exact_match_url = None
        for link in links:
            if "exact" in link["text"].lower() or "exact" in link["href"].lower():
                exact_match_url = link["href"]
                print(f"\n    ✅ Found Exact Match link: {link['href']}")
                break

        if exact_match_url:
            print("\n[3] Navigating to Exact Match URL...")
            await page.goto(exact_match_url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(3)
        else:
            print("\n    ⚠ Could not find Exact Match tab automatically.")
            print("    Please click 'Exact match' in the browser window now.")
            print("    Waiting 15 seconds...")
            await asyncio.sleep(15)

        final_url = page.url
        print(f"\n[4] Final URL:\n    {final_url}")

        parsed = urlparse(final_url)
        params = parse_qs(parsed.query)
        print(f"\n    Query params: {json.dumps({k: v for k, v in params.items()}, indent=2)}")

        html = await page.content()
        print(f"\n    HTML length: {len(html)} chars")

        # -------------------------------------------------------
        # Save results
        # -------------------------------------------------------
        output = {
            "test_image_url": TEST_IMAGE_URL,
            "lens_entry_url": lens_url,
            "exact_match_url": final_url,
            "url_params": {k: v for k, v in params.items()},
            "all_google_requests": [
                {"method": r["method"], "url": r["url"]}
                for r in captured_requests
            ],
        }

        with open("investigation_results.json", "w") as f:
            json.dump(output, f, indent=2)

        with open("exact_match_page.html", "w", encoding="utf-8") as f:
            f.write(html)

        print("\n✅ Saved: investigation_results.json + exact_match_page.html")
        print("\nShare investigation_results.json — that's what we need to see next.")

        await asyncio.sleep(3)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
