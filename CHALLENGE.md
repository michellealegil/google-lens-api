# MrScraper Coding Challenge

## Role
Data Acquisition Engineer

## Objective
Build a scalable and reliable API that accepts an image URL, performs a Google Lens visual search, navigates to the Exact Match results page, and returns the full page HTML as the API response.

## Manual Flow (what the API replicates)
1. Go to google.com
2. Click the Google Lens camera icon in the search bar
3. Paste the image URL and press "Search"
4. After redirection to the "All" results page, navigate to the "Exact match" tab
5. Return the full HTML of that Exact Match page as the API response

## Planned Approach
- **Framework:** FastAPI (Python)
- **Browser automation:** Playwright (async, handles JS-heavy pages, stealth-friendly)
- **Architecture:** Single working version first, then add scalability

## Key Challenges
- Google bot detection — need realistic browser fingerprint
- Dynamic page elements — proper wait conditions, not time.sleep
- Scalability — browser instances are expensive, need pooling or async management

## Files
- `main.py` — FastAPI app
- `scraper.py` — Playwright logic

## Status
- [ ] Working single-request version
- [ ] Bot detection handling
- [ ] Scalability / browser pooling
- [ ] Final cleanup and submission
