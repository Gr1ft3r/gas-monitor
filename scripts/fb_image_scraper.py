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


def fetch_latest_price_image(page_url: str) -> str | None:
    """
    Fetch the latest fuel-price image URL from a public Facebook page.

    Strategy:
      1. Use facebook-scraper to pull recent posts (no login needed for public pages)
      2. Filter by price-related keywords
      3. Return the first image URL found

    Returns:
        URL string of the image, or None if not found.
    """
    try:
        from facebook_scraper import get_posts
    except ImportError:
        raise ImportError(
            "facebook-scraper is not installed.\n"
            "Run: pip install facebook-scraper"
        )

    # Extract page identifier from URL
    # e.g. https://www.facebook.com/pagename → "pagename"
    page_id = page_url.rstrip("/").split("/")[-1]
    print(f"[FB] Fetching recent posts from page: {page_id}")

    try:
        for post in get_posts(page_id, pages=3, options={"images": True, "posts_per_page": 10}):
            # Collect text from ALL fields facebook-scraper may populate
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
                print(f"  Available fields: {[k for k in post.keys()]}")
                print(f"  Combined text: {post_text[:120]!r}")
                print(f"  Images found: {len(images)}, single image: {bool(image)}")

            # Fallback: if the env var FB_GRAB_LATEST is set, take the first
            # post that has an image regardless of text content — useful when
            # the page posts price images with no caption.
            grab_latest = os.environ.get("FB_GRAB_LATEST", "false").lower() == "true"
            has_image   = bool(images or image)

            if not grab_latest and not _is_price_post(post_text):
                if DEBUG:
                    print(f"  ↳ Skipped (no matching keywords)")
                continue

            if grab_latest and not has_image:
                if DEBUG:
                    print(f"  ↳ Skipped (FB_GRAB_LATEST=true but no image)")
                continue

            # Prefer the list of images first, then the single image field
            if images:
                print(f"[FB] ✅ Price post found — using first image ({len(images)} total)")
                return images[0]
            elif image:
                print(f"[FB] ✅ Price post found — using single image field")
                return image

    except Exception as e:
        print(f"[FB] ❌ facebook-scraper error: {e}")
        print("     Tip: Facebook may have changed their page structure.")
        print("     Try updating: pip install --upgrade facebook-scraper")

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