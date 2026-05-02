#!/usr/bin/env python3
"""
fb_image_scraper.py — Benguet Gas Monitor
==========================================
Scans a public Facebook page for the latest fuel-price image post,
downloads the image, extracts fuel/price pairs using OpenAI Vision,
and upserts them into Supabase.

Environment variables (set as GitHub Secrets or in .env):
    SUPABASE_URL        — e.g. https://xxxx.supabase.co
    SUPABASE_KEY        — service-role or anon key
    OPENAI_API_KEY      — for GPT-4o Vision
    FB_PAGE_URL         — public Facebook page URL to monitor
                          e.g. https://www.facebook.com/somepage
    DRY_RUN             — set to "true" to preview without DB writes
    DEBUG               — set to "true" for verbose logging

Usage:
    python fb_image_scraper.py
"""

import os
import re
import json
import base64
import requests
from datetime import datetime
from io import BytesIO
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────

DRY_RUN        = os.environ.get("DRY_RUN",  "false").strip().lower() == "true"
DEBUG          = os.environ.get("DEBUG",    "false").strip().lower() == "true"
SUPABASE_URL   = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_KEY   = os.environ.get("SUPABASE_KEY", "").strip()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
FB_PAGE_URL    = os.environ.get("FB_PAGE_URL", "").strip()

SUPABASE_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}

# Keywords that help us identify the fuel-price post among all page posts
PRICE_POST_KEYWORDS = [
    # English terms
    "price", "fuel", "pump", "monitoring", "as of", "per liter",
    "pump price", "oil price", "fuel price",
    # Brand names
    "petron", "shell", "caltex", "cleanfuel", "seaoil", "phoenix",
    # Filipino terms
    "presyo", "gasolina", "diesel", "bawat litro", "piso",
    # Currency markers
    "₱", "php",
]

# City/branch heuristics — update to match what the FB page typically posts
TARGET_CITY = os.environ.get("FB_TARGET_CITY", "Baguio City")


# ─────────────────────────────────────────────────────────────
# STEP 1 — FETCH LATEST FACEBOOK POST IMAGE
# ─────────────────────────────────────────────────────────────

def _is_price_post(text: str) -> bool:
    """Return True if post text looks like a fuel price announcement."""
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in PRICE_POST_KEYWORDS)


def _scrape_with_facebook_scraper(page_id: str) -> str | None:
    """
    Strategy A — use facebook-scraper library (works on public pages,
    but may return 0 posts if Facebook's structure changed or blocks the request).
    Requires: pip install facebook-scraper lxml_html_clean
    Optional: set FB_COOKIES env var to a Netscape-format cookies file path for
              authenticated access (greatly improves reliability).
    """
    try:
        from facebook_scraper import get_posts
    except ImportError:
        raise ImportError(
            "facebook-scraper is not installed.\n"
            "Run: pip install facebook-scraper lxml_html_clean"
        )

    grab_latest = os.environ.get("FB_GRAB_LATEST", "false").lower() == "true"
    cookies     = os.environ.get("FB_COOKIES", None)   # path to Netscape cookies file

    options = {
        "images":           True,
        "posts_per_page":   10,
        "allow_extra_requests": True,
    }
    if cookies:
        print(f"[FB-A] Using cookies file: {cookies}")

    kwargs = dict(pages=5, options=options)
    if cookies:
        kwargs["cookies"] = cookies

    post_count = 0
    try:
        for post in get_posts(page_id, **kwargs):
            post_count += 1

            post_text = " ".join(filter(None, [
                post.get("text"),
                post.get("post_text"),
                post.get("header"),
                post.get("title"),
                post.get("shared_text"),
                post.get("link_text"),
            ]))
            images = post.get("images") or []
            image  = post.get("image")

            if DEBUG:
                print(f"  [A] Post #{post_count} fields: {list(post.keys())}")
                print(f"  [A] Text: {post_text[:120]!r}")
                print(f"  [A] Images: {len(images)} list + {'1' if image else '0'} single")

            has_image = bool(images or image)

            if grab_latest:
                if not has_image:
                    if DEBUG: print(f"  [A] ↳ Skipped (no image)")
                    continue
            else:
                if not _is_price_post(post_text):
                    if DEBUG: print(f"  [A] ↳ Skipped (no matching keywords)")
                    continue

            if images:
                print(f"[FB-A] ✅ Match on post #{post_count} — {len(images)} image(s)")
                return images[0]
            elif image:
                print(f"[FB-A] ✅ Match on post #{post_count} — single image field")
                return image

    except Exception as e:
        print(f"[FB-A] ❌ facebook-scraper error: {e}")
        if "lxml" in str(e).lower():
            print("     Fix: pip install lxml_html_clean")
        else:
            print("     Tip: pip install --upgrade facebook-scraper")

    if post_count == 0:
        print("[FB-A] ⚠️  facebook-scraper returned 0 posts.")
        print("     This usually means Facebook blocked the unauthenticated request.")
        print("     → Try setting FB_COOKIES to a Netscape cookies file.")
        print("     → Or set FB_GRAB_LATEST=true if all posts on this page are price posts.")
    else:
        print(f"[FB-A] ⚠️  Scanned {post_count} posts — none matched price keywords.")

    return None


def _scrape_html_endpoint(label: str, url: str, story_selector, img_filter) -> str | None:
    """
    Generic HTML scraper helper. Fetches `url`, finds story blocks via `story_selector`,
    and returns the first image src that passes `img_filter`.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print(f"[{label}] BeautifulSoup not installed — pip install beautifulsoup4")
        return None

    ua_desktop = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    ua_mobile = (
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Mobile Safari/537.36"
    )
    headers = {
        "User-Agent": ua_desktop if "mbasic" not in url else ua_mobile,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
    }

    print(f"[{label}] Trying {url} ...")
    try:
        resp = requests.get(url, headers=headers, timeout=25, allow_redirects=True)
    except Exception as e:
        print(f"[{label}] ❌ Request error: {e}")
        return None

    if resp.status_code != 200:
        print(f"[{label}] ❌ HTTP {resp.status_code}")
        if DEBUG:
            print(f"  [{label}] Response snippet: {resp.text[:300]!r}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    grab_latest = os.environ.get("FB_GRAB_LATEST", "false").lower() == "true"
    post_count  = 0

    for story in story_selector(soup):
        post_count += 1
        story_text = story.get_text(" ", strip=True)

        if DEBUG:
            print(f"  [{label}] Post #{post_count}: {story_text[:120]!r}")

        if not grab_latest and not _is_price_post(story_text):
            if DEBUG: print(f"  [{label}] ↳ Skipped (no keywords)")
            continue

        for img in story.find_all("img"):
            src = img.get("src", "")
            if img_filter(src):
                print(f"[{label}] ✅ Match on post #{post_count}")
                if DEBUG: print(f"  [{label}] src: {src[:120]}")
                return src

    if post_count == 0:
        print(f"[{label}] ⚠️  0 post blocks found — page structure may have changed.")
        if DEBUG:
            # Dump a snippet so we can diagnose what FB actually returned
            print(f"  [{label}] Page title: {soup.title.string if soup.title else 'N/A'!r}")
            print(f"  [{label}] Body snippet: {soup.body.get_text(' ', strip=True)[:400] if soup.body else 'N/A'!r}")
    else:
        print(f"[{label}] ⚠️  Scanned {post_count} posts — none matched price keywords.")

    return None


def _scrape_with_requests(page_id: str) -> str | None:
    """
    Strategy B — tries three HTML endpoints in order:
      B1) mbasic.facebook.com  (lightest, most scraper-friendly)
      B2) m.facebook.com       (mobile site, richer markup)
      B3) www.facebook.com     (full site — JS-heavy but sometimes has og:image meta tags)
    """
    grab_latest = os.environ.get("FB_GRAB_LATEST", "false").lower() == "true"

    # ── B1: mbasic ──────────────────────────────────────────────────────────
    def mbasic_stories(soup):
        # mbasic wraps each post in a <div id="story_...">
        stories = soup.find_all("div", id=lambda x: x and x.startswith("story_"))
        if not stories:
            # Newer mbasic layout sometimes uses article tags
            stories = soup.find_all("article")
        return stories

    def mbasic_img(src):
        return bool(src) and "static" not in src and "emoji" not in src and len(src) > 30

    result = _scrape_html_endpoint(
        "FB-B1",
        f"https://mbasic.facebook.com/{page_id}",
        mbasic_stories,
        mbasic_img,
    )
    if result:
        return result

    # ── B2: m.facebook.com ──────────────────────────────────────────────────
    def mobile_stories(soup):
        # Mobile FB wraps stories in div[data-ft] or div[data-store]
        stories = soup.find_all("div", attrs={"data-ft": True})
        if not stories:
            stories = soup.find_all("div", class_=lambda c: c and "story" in c.lower())
        return stories

    def mobile_img(src):
        return bool(src) and "scontent" in src and len(src) > 30

    result = _scrape_html_endpoint(
        "FB-B2",
        f"https://m.facebook.com/{page_id}",
        mobile_stories,
        mobile_img,
    )
    if result:
        return result

    # ── B3: og:image meta tag from www.facebook.com ─────────────────────────
    # FB's www site is JS-heavy but the <meta property="og:image"> in the <head>
    # often still contains the latest post image for public pages.
    try:
        from bs4 import BeautifulSoup
        headers_www = {
            "User-Agent": (
                "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }
        print(f"[FB-B3] Trying og:image meta from www.facebook.com/{page_id} ...")
        r = requests.get(
            f"https://www.facebook.com/{page_id}",
            headers=headers_www, timeout=25, allow_redirects=True,
        )
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            og = soup.find("meta", property="og:image")
            if og and og.get("content"):
                img_url = og["content"]
                if grab_latest or _is_price_post(soup.get_text(" ", strip=True)[:2000]):
                    print(f"[FB-B3] ✅ Found og:image meta tag")
                    if DEBUG: print(f"  [FB-B3] og:image: {img_url[:120]}")
                    return img_url
                else:
                    print(f"[FB-B3] ⚠️  og:image found but page text has no price keywords.")
                    print(f"         Set FB_GRAB_LATEST=true to use it anyway.")
        else:
            print(f"[FB-B3] ❌ HTTP {r.status_code}")
    except Exception as e:
        print(f"[FB-B3] ❌ Error: {e}")

    return None


def fetch_latest_price_image(page_url: str) -> str | None:
    """
    Fetch the latest fuel-price image URL from a public Facebook page.

    Tries two strategies in order:
      A) facebook-scraper library  (best quality, may be blocked without cookies)
      B) Direct mbasic.facebook.com HTML scrape  (fallback, no extra deps)

    Environment variables:
      FB_COOKIES     — path to Netscape cookies file → makes Strategy A much more reliable
      FB_GRAB_LATEST — "true" → skip keyword matching, grab first post with an image
      DEBUG          — "true" → verbose per-post logging

    Returns:
        URL string of the image, or None if neither strategy found anything.
    """
    page_id = page_url.rstrip("/").split("/")[-1]
    print(f"[FB] Fetching recent posts from page: {page_id}")

    # ── Strategy A ─────────────────────────────────────────
    print("[FB] Trying Strategy A: facebook-scraper ...")
    result = _scrape_with_facebook_scraper(page_id)
    if result:
        return result

    # ── Strategy B ─────────────────────────────────────────
    print("[FB] Strategy A found nothing. Trying Strategy B: HTML scrape ...")
    result = _scrape_with_requests(page_id)
    if result:
        return result

    print("[FB] ⚠️  No price image found in recent posts.")
    return None


# ─────────────────────────────────────────────────────────────
# STEP 2 — EXTRACT PRICES FROM IMAGE VIA GPT-4o VISION
# ─────────────────────────────────────────────────────────────

EXTRACTION_PROMPT = """
You are a fuel price data extractor for a Philippine community app.

The image shows a fuel price announcement posted by a gas station or
local page in the Cordillera region (Baguio City area, Philippines).

Extract ALL fuel type + price pairs visible in the image.

Rules:
- Prices are in Philippine Peso (₱), typically between ₱30 and ₱250 per liter.
- Fuel names should match common Philippine names:
    Diesel, Unleaded 91, Premium 95, Blaze 100, Diesel Max,
    V-Power Diesel, FuelSave Diesel, Turbo Diesel, Kerosene, etc.
- If a station name or brand is visible, include it.
- If multiple stations are in the image, extract each separately.
- Ignore decorative text, dates, and slogans.

Respond ONLY with valid JSON in this exact format:
{
  "station_name": "Petron - Loakan Road" or null if not visible,
  "city": "Baguio City" or the city shown,
  "date_posted": "YYYY-MM-DD" or null,
  "prices": [
    {"fuel_type": "Diesel Max", "price": 68.50},
    {"fuel_type": "Super Xtra 91", "price": 62.25}
  ]
}

If you cannot find any price data, return: {"prices": []}
"""


def image_url_to_base64(url: str) -> str:
    """Download image and convert to base64 for Vision API."""
    resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    return base64.b64encode(resp.content).decode("utf-8")


def extract_prices_from_image(image_url: str) -> dict:
    """
    Send image to GPT-4o Vision and parse extracted price data.

    Returns dict like:
    {
        "station_name": "Petron - Loakan Road",
        "city": "Baguio City",
        "prices": [{"fuel_type": "Diesel Max", "price": 68.50}, ...]
    }
    """
    print(f"[OCR] Sending image to GPT-4o Vision: {image_url[:60]}...")

    # Convert to base64 (more reliable than passing raw FB URLs to OpenAI)
    try:
        b64_image = image_url_to_base64(image_url)
        image_content = {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"},
        }
    except Exception as e:
        print(f"[OCR] ⚠️  Could not download image ({e}), trying direct URL instead.")
        image_content = {
            "type": "image_url",
            "image_url": {"url": image_url},
        }

    payload = {
        "model": "gpt-4o",
        "max_tokens": 1000,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": EXTRACTION_PROMPT},
                    image_content,
                ],
            }
        ],
    }

    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type":  "application/json",
        },
        json=payload,
        timeout=60,
    )
    response.raise_for_status()
    raw_text = response.json()["choices"][0]["message"]["content"].strip()

    if DEBUG:
        print(f"[OCR] Raw GPT response:\n{raw_text}")

    # Strip markdown code fences if present
    raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.MULTILINE)
    raw_text = re.sub(r"\s*```$",           "", raw_text, flags=re.MULTILINE)

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        print(f"[OCR] ❌ Failed to parse JSON: {e}")
        print(f"[OCR] Raw text was: {raw_text}")
        return {"prices": []}

    prices = data.get("prices", [])
    print(f"[OCR] ✅ Extracted {len(prices)} fuel price(s) from image.")
    return data


# ─────────────────────────────────────────────────────────────
# STEP 3 — VALIDATE EXTRACTED DATA
# ─────────────────────────────────────────────────────────────

def validate_extracted(data: dict) -> list[dict]:
    """
    Filter extracted prices to only realistic values.
    Returns list of clean price records.
    """
    valid = []
    for item in data.get("prices", []):
        fuel_type = str(item.get("fuel_type", "")).strip()
        try:
            price = float(item.get("price", 0))
        except (ValueError, TypeError):
            continue

        if not fuel_type:
            print(f"[VALIDATE] Skipping — missing fuel type")
            continue
        if not (30.0 <= price <= 250.0):
            print(f"[VALIDATE] Skipping {fuel_type} — price ₱{price} out of range (30–250)")
            continue

        valid.append({"fuel_type": fuel_type, "price": price})

    print(f"[VALIDATE] {len(valid)}/{len(data.get('prices', []))} entries passed validation.")
    return valid


# ─────────────────────────────────────────────────────────────
# STEP 4 — UPSERT INTO SUPABASE
# ─────────────────────────────────────────────────────────────

def _get_or_create_station(station_name: str, city: str) -> str | None:
    """
    Look up station by name. Create it (Unverified) if not found.
    Returns station ID or None on failure.
    """
    # Try exact match first
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/stations"
        f"?name=ilike.{requests.utils.quote(station_name)}&select=id,status&limit=1",
        headers=SUPABASE_HEADERS,
    )
    r.raise_for_status()
    rows = r.json()

    if rows:
        station_id = rows[0]["id"]
        if DEBUG:
            print(f"[DB] Found existing station: {station_name} → {station_id}")
        return station_id

    # Not found — create it
    print(f"[DB] Creating new station: {station_name} in {city}")
    insert = requests.post(
        f"{SUPABASE_URL}/rest/v1/stations",
        headers=SUPABASE_HEADERS,
        json={"name": station_name, "city": city, "status": "Unverified"},
    )
    insert.raise_for_status()
    created = insert.json()
    if created:
        return created[0]["id"]
    return None


def upsert_prices(data: dict, valid_prices: list[dict]):
    """
    For each valid price:
     1. Resolve station ID (create if needed)
     2. Archive existing active price for that fuel
     3. Insert new Unverified price row
    """
    prefix = "[DRY RUN] " if DRY_RUN else ""

    station_name = data.get("station_name")
    city         = data.get("city") or TARGET_CITY

    if not station_name:
        print(f"[DB] ⚠️  No station name in extracted data — cannot upsert.")
        print("     You may want to hardcode FB_STATION_NAME in your .env if")
        print("     the Facebook page always posts for the same station.")
        return

    print(f"\n[DB] {prefix}Upserting {len(valid_prices)} prices for: {station_name} ({city})")

    if DRY_RUN:
        print(f"  {'Fuel Type':<35} {'Price':>8}")
        print(f"  {'-'*35} {'-'*8}")
        for p in valid_prices:
            print(f"  {p['fuel_type']:<35}  ₱{p['price']:>6.2f}")
        print(f"\n  [DRY RUN] No database changes were made.")
        return

    station_id = _get_or_create_station(station_name, city)
    if not station_id:
        print("[DB] ❌ Could not resolve station ID. Aborting.")
        return

    updated = 0
    for p in valid_prices:
        fuel_type = p["fuel_type"]
        new_price = p["price"]

        # Fetch current active price for this fuel
        current_r = requests.get(
            f"{SUPABASE_URL}/rest/v1/prices"
            f"?station_id=eq.{station_id}"
            f"&fuel_type=eq.{requests.utils.quote(fuel_type)}"
            f"&status=neq.Archived&select=id,price&limit=1",
            headers=SUPABASE_HEADERS,
        )
        current_r.raise_for_status()
        current_rows = current_r.json()

        old_price = None
        if current_rows:
            old_price = float(current_rows[0]["price"])
            # Skip if price hasn't changed
            if old_price == new_price:
                print(f"  — {fuel_type}: ₱{new_price} (no change, skipping)")
                continue
            # Archive old row
            requests.patch(
                f"{SUPABASE_URL}/rest/v1/prices?id=eq.{current_rows[0]['id']}",
                headers=SUPABASE_HEADERS,
                json={"status": "Archived"},
            )

        # Insert new row
        row = {
            "station_id": station_id,
            "fuel_type":  fuel_type,
            "price":      new_price,
            "status":     "Unverified",
            "upvotes":    1,
        }
        if old_price is not None:
            row["old_price"] = old_price

        insert_r = requests.post(
            f"{SUPABASE_URL}/rest/v1/prices",
            headers=SUPABASE_HEADERS,
            json=row,
        )
        if insert_r.status_code in (200, 201):
            direction = "▲" if (old_price and new_price > old_price) else ("▼" if old_price else "NEW")
            old_str   = f"₱{old_price:.2f} → " if old_price else ""
            print(f"  {direction} {fuel_type}: {old_str}₱{new_price:.2f}")
            updated += 1
        else:
            print(f"  ❌ Failed to insert {fuel_type}: {insert_r.text}")

    print(f"\n[DB] Done. {updated} prices upserted.")


# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 65)
    print("  Benguet Gas Monitor — Facebook Image Price Scraper")
    print(f"  Run time : {datetime.now().strftime('%A, %B %d %Y %I:%M %p')}")
    print(f"  Mode     : {'DRY RUN (no DB writes)' if DRY_RUN else 'LIVE (will write to DB)'}")
    print(f"  Target   : {FB_PAGE_URL or '⚠️  FB_PAGE_URL not set'}")
    print("=" * 65)

    # Validate environment
    missing = [v for v in ["SUPABASE_URL", "SUPABASE_KEY", "OPENAI_API_KEY", "FB_PAGE_URL"]
               if not os.environ.get(v)]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}\n"
            "Add them to your .env file or GitHub Secrets."
        )

    # Pipeline
    image_url = fetch_latest_price_image(FB_PAGE_URL)
    if not image_url:
        print("\n⚠️  No price image found. Nothing to update.")
        exit(0)

    extracted = extract_prices_from_image(image_url)
    if not extracted.get("prices"):
        print("\n⚠️  No prices extracted from image. Nothing to update.")
        exit(0)

    valid_prices = validate_extracted(extracted)
    if not valid_prices:
        print("\n⚠️  No valid prices after validation. Nothing to update.")
        exit(0)

    upsert_prices(extracted, valid_prices)