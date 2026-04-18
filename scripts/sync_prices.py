import os
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# --- CONFIGURATION ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# Read DRY_RUN from environment (set via GitHub Actions workflow_dispatch input)
# "true" (string) = preview only, no DB writes. Anything else = live mode.
DRY_RUN = os.environ.get("DRY_RUN", "false").strip().lower() == "true"

SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

# Safety cap: rejects any extracted value above this (protects against bad regex matches).
# Historical PH max is ~P25/L (April 2026 mega-rollback). 30 gives a safe margin.
MAX_SAFE_ADJUSTMENT = 30.0


# ─────────────────────────────────────────────
# SCRAPER
# ─────────────────────────────────────────────
def scrape_doe_advisory():
    """
    Scrapes Philippine news RSS feeds for the latest DOE fuel price advisory.
    Returns signed floats: negative = rollback/cut, positive = hike.
    Tries 3 sources in priority order; exits safely if all fail.
    """
    print(f"[{datetime.now()}] Starting DOE advisory scrape...")
    result = {"gasoline_change": 0.0, "diesel_change": 0.0, "kerosene_change": 0.0}

    sources = [
        {"name": "GMA News RSS",    "url": "https://data.gmanetwork.com/gno/rss/money/feed.xml"},
        {"name": "PhilStar RSS",    "url": "https://www.philstar.com/rss/business"},
        {"name": "Google News RSS", "url": "https://news.google.com/rss/search?q=oil+price+update+philippines+gasoline+diesel+when:3d&hl=en-PH&gl=PH&ceid=PH:en"},
    ]
    scrape_headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    fuel_keywords  = ["fuel", "oil price", "gasoline", "diesel", "petrol", "pump price", "rollback", "hike"]

    raw_news_text = ""

    for source in sources:
        try:
            print(f"  Trying: {source['name']}...")
            resp = requests.get(source["url"], headers=scrape_headers, timeout=15)
            resp.raise_for_status()

            soup  = BeautifulSoup(resp.content, "lxml-xml")
            items = soup.find_all("item")[:5]
            if not items:
                print(f"  No items in {source['name']}, trying next...")
                continue

            relevant = [
                i for i in items
                if any(
                    kw in (i.title.text + " " + (i.description.text if i.description else "")).lower()
                    for kw in fuel_keywords
                )
            ]
            if not relevant:
                print(f"  No fuel headlines in {source['name']}, trying next...")
                continue

            raw_news_text = " ".join([
                i.title.text + " " + (i.description.text if i.description else "")
                for i in relevant
            ])
            print(f"  Got {len(relevant)} relevant article(s) from {source['name']}")
            break

        except Exception as e:
            print(f"  {source['name']} failed: {e}")
            continue

    if not raw_news_text:
        print("All sources failed. Database unchanged.")
        return result

    result["gasoline_change"] = _extract_adjustment(["gasoline", "gas", "unleaded", "petrol"], raw_news_text)
    result["diesel_change"]   = _extract_adjustment(["diesel"], raw_news_text)
    result["kerosene_change"] = _extract_adjustment(["kerosene", "gaas"], raw_news_text)

    print(f"\nExtracted adjustments: {result}")
    return result


def _extract_adjustment(fuel_keywords_list, raw_text):
    """
    Extracts a signed price adjustment for a given fuel type from raw RSS text.

    Fixes vs original:
    - Strips HTML tags first (prevents img/src URL numbers from polluting regex)
    - Added Pattern 4: action → price → fuel
      (covers GMA headline style: "rollback of P3.41/liter for gasoline")
    - Safety cap raised to 30.0 (handles large April 2026-style rollbacks)
    """
    # Strip HTML before regex — removes <img src="...2026_04_18..."> noise
    clean_text = BeautifulSoup(raw_text, "html.parser").get_text().lower()

    fuel_pattern = "|".join(fuel_keywords_list)
    price_token  = r"(?:php|p|peso)?\s*(\d+\.?\d*)"
    action_token = r"(?:rollback|hike|increase|decrease|cut|drop|rise|down|up)"
    decrease_words = ["rollback", "decrease", "down", "cut", "slash", "lower", "drop", "reduce", "fell", "fall"]
    increase_words = ["hike", "increase", "up", "rise", "surge", "jump", "climb", "higher"]

    patterns = [
        # Pattern 1: "diesel rollback of P1.50"         — fuel → action → price
        rf"(?:{fuel_pattern}).{{0,60}}?{action_token}.{{0,30}}?{price_token}",
        # Pattern 2: "P1.50 rollback for diesel"        — price → action → fuel
        rf"{price_token}.{{0,30}}?{action_token}.{{0,40}}?(?:{fuel_pattern})",
        # Pattern 3: "diesel: down P1.50"               — fuel → direction → price
        rf"(?:{fuel_pattern}).{{0,20}}?(?:down|up).{{0,10}}?{price_token}",
        # Pattern 4: "rollback of P3.41/liter for gas"  — action → price → fuel (GMA style)
        rf"{action_token}.{{0,20}}?{price_token}.{{0,20}}?(?:{fuel_pattern})",
    ]

    for i, pattern in enumerate(patterns, 1):
        match = re.search(pattern, clean_text)
        if match:
            try:
                amount = float(match.group(1))
                if amount > MAX_SAFE_ADJUSTMENT:
                    ctx = clean_text[max(0, match.start()-40):match.end()+40]
                    print(f"  Skipping {amount} — exceeds cap ({MAX_SAFE_ADJUSTMENT}). Context: ...{ctx}...")
                    continue
                ctx_start = max(0, match.start() - 60)
                ctx_end   = min(len(clean_text), match.end() + 60)
                context   = clean_text[ctx_start:ctx_end]
                is_decrease = any(w in context for w in decrease_words)
                is_increase = any(w in context for w in increase_words)
                if not is_decrease and not is_increase:
                    continue
                signed = -amount if is_decrease else amount
                print(f"  [Pattern {i}] Found {'+' if signed > 0 else ''}{signed:.2f} for [{fuel_pattern}]")
                return signed
            except ValueError:
                continue
    return 0.0


# ─────────────────────────────────────────────
# SUPABASE UPDATER
# ─────────────────────────────────────────────
def apply_doe_updates(adjustments):
    """
    Applies DOE price adjustments to all Verified rows in Supabase.

    Live mode:
      - Archives the old Verified row (status → "Archived")
      - Inserts a new Unverified row with old_price saved for "Was P{X}" UI label

    Dry-run mode:
      - Fetches Verified rows and computes what WOULD change
      - Prints a full preview table
      - Makes ZERO writes to the database
    """
    prefix = "[DRY RUN] " if DRY_RUN else ""

    if all(v == 0.0 for v in adjustments.values()):
        print(f"\n{prefix}No adjustments to apply. Database unchanged.")
        return

    print(f"\n[{datetime.now()}] {prefix}Fetching Verified prices from Supabase...")
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/prices?status=eq.Verified&select=id,fuel_type,price,station_id",
        headers=SUPABASE_HEADERS
    )
    resp.raise_for_status()
    verified = resp.json()
    print(f"  Found {len(verified)} Verified rows.")

    if DRY_RUN:
        _dry_run_preview(verified, adjustments)
        return

    # ── LIVE MODE ──
    updated = skipped = 0
    for item in verified:
        fuel      = item["fuel_type"].lower()
        old_price = float(item["price"])

        if "diesel" in fuel:
            delta = adjustments["diesel_change"]
        elif "kerosene" in fuel or "gaas" in fuel:
            delta = adjustments["kerosene_change"]
        else:
            delta = adjustments["gasoline_change"]

        if delta == 0.0:
            skipped += 1
            continue

        new_price = round(old_price + delta, 2)
        if new_price < 20.0:
            print(f"  Skipping {item['fuel_type']} — computed P{new_price} is unrealistically low.")
            skipped += 1
            continue

        # Step 1: Archive old row
        archive = requests.patch(
            f"{SUPABASE_URL}/rest/v1/prices?id=eq.{item['id']}",
            headers=SUPABASE_HEADERS,
            json={"status": "Archived"}
        )
        if archive.status_code not in (200, 204):
            print(f"  Failed to archive ID {item['id']}: {archive.text}")
            continue

        # Step 2: Insert fresh Unverified row with old_price preserved
        insert = requests.post(
            f"{SUPABASE_URL}/rest/v1/prices",
            headers=SUPABASE_HEADERS,
            json={
                "station_id": item["station_id"],
                "fuel_type":  item["fuel_type"],
                "price":      new_price,
                "old_price":  old_price,
                "status":     "Unverified",
                "upvotes":    0,
            }
        )
        if insert.status_code in (200, 201):
            direction = "UP  " if delta > 0 else "DOWN"
            print(f"  [{direction}] {item['fuel_type']}: P{old_price} -> P{new_price} (d={delta:+})")
            updated += 1
        else:
            print(f"  Insert failed for {item['fuel_type']}: {insert.text}")

    print(f"\nDone. {updated} updated, {skipped} skipped.")


def _dry_run_preview(verified, adjustments):
    """
    Prints a formatted preview of all changes that WOULD be applied.
    No database writes are made.
    """
    print("\n" + "=" * 60)
    print("  DRY RUN PREVIEW — no database changes will be made")
    print("=" * 60)
    print(f"  Adjustments to apply:")
    print(f"    Gasoline : {adjustments['gasoline_change']:+.2f} PHP/L")
    print(f"    Diesel   : {adjustments['diesel_change']:+.2f} PHP/L")
    print(f"    Kerosene : {adjustments['kerosene_change']:+.2f} PHP/L")
    print()

    would_update  = []
    would_skip    = []
    would_guard   = []

    for item in verified:
        fuel      = item["fuel_type"].lower()
        old_price = float(item["price"])

        if "diesel" in fuel:
            delta = adjustments["diesel_change"]
        elif "kerosene" in fuel or "gaas" in fuel:
            delta = adjustments["kerosene_change"]
        else:
            delta = adjustments["gasoline_change"]

        if delta == 0.0:
            would_skip.append(item["fuel_type"])
            continue

        new_price = round(old_price + delta, 2)
        if new_price < 20.0:
            would_guard.append((item["fuel_type"], old_price, new_price))
            continue

        would_update.append((item["fuel_type"], old_price, new_price, delta))

    if would_update:
        print(f"  WOULD UPDATE ({len(would_update)} rows):")
        print(f"  {'Fuel Type':<25} {'Old Price':>10}  {'New Price':>10}  {'Change':>8}  Action")
        print(f"  {'-'*25} {'-'*10}  {'-'*10}  {'-'*8}  ------")
        for fuel_type, old, new, d in would_update:
            arrow = "⬆  HIKE    " if d > 0 else "⬇  ROLLBACK"
            print(f"  {fuel_type:<25}  P{old:>8.2f}  P{new:>8.2f}  {d:>+7.2f}  {arrow}")

    if would_guard:
        print(f"\n  WOULD SKIP — price guard triggered ({len(would_guard)} rows):")
        for fuel_type, old, new in would_guard:
            print(f"    {fuel_type}: P{old:.2f} -> P{new:.2f} (below P20 floor)")

    if would_skip:
        print(f"\n  WOULD SKIP — no applicable adjustment ({len(would_skip)} rows):")
        for ft in would_skip:
            print(f"    {ft}")

    print()
    print(f"  Summary: {len(would_update)} would update, "
          f"{len(would_skip) + len(would_guard)} would skip")
    print("=" * 60)
    print("  To apply these changes, re-run with DRY_RUN=false")
    print("=" * 60)


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("  Benguet Gas Monitor - DOE Auto-Sync Script")
    print(f"  Run time : {datetime.now().strftime('%A, %B %d %Y %I:%M %p')}")
    print(f"  Mode     : {'DRY RUN (no DB writes)' if DRY_RUN else 'LIVE (will write to DB)'}")
    print("=" * 60)

    if not SUPABASE_URL or not SUPABASE_KEY:
        raise EnvironmentError(
            "SUPABASE_URL or SUPABASE_KEY not set.\n"
            "Add them as GitHub Secrets or in a local .env file."
        )

    apply_doe_updates(scrape_doe_advisory())