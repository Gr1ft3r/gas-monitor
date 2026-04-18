import os
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# --- CONFIGURATION ---
# These are loaded from GitHub Secrets (never hardcode these!)
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

# Safety clamp: never apply an adjustment larger than this (catches bad scrapes)
MAX_SAFE_ADJUSTMENT = 30.0

# ─────────────────────────────────────────────
# SCRAPER — tries multiple sources in priority order
# ─────────────────────────────────────────────
def scrape_doe_advisory():
    """
    Attempts to extract gasoline, diesel, and kerosene price adjustments
    from Philippine news sources. Returns a dict with float adjustments.
    Positive = price hike, Negative = rollback/cut.
    """
    print(f"[{datetime.now()}] Starting DOE advisory scrape...")

    result = {"gasoline_change": 0.0, "diesel_change": 0.0, "kerosene_change": 0.0}

    sources = [
        {
            "name": "GMA News RSS",
            "url": "https://data.gmanetwork.com/gno/rss/money/feed.xml",
        },
        {
            "name": "PhilStar RSS",
            "url": "https://www.philstar.com/rss/business",
        },
        {
            "name": "Google News RSS",
            "url": "https://news.google.com/rss/search?q=oil+price+update+philippines+gasoline+diesel+when:3d&hl=en-PH&gl=PH&ceid=PH:en",
        },
    ]

    scrape_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    news_text = ""

    for source in sources:
        try:
            print(f"  Trying source: {source['name']}...")
            resp = requests.get(source["url"], headers=scrape_headers, timeout=15)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.content, "lxml-xml")
            items = soup.find_all("item")[:5]

            if not items:
                print(f"  No items found in {source['name']}, trying next...")
                continue

            fuel_keywords = ["fuel", "oil price", "gasoline", "diesel", "petrol", "pump price", "rollback", "hike"]
            relevant = [
                i for i in items
                if any(kw in (i.title.text + " " + (i.description.text if i.description else "")).lower()
                       for kw in fuel_keywords)
            ]

            if not relevant:
                print(f"  No fuel-related headlines in {source['name']}, trying next...")
                continue

            news_text = " ".join([
                i.title.text + " " + (i.description.text if i.description else "")
                for i in relevant
            ]).lower()

            print(f"  Got {len(relevant)} relevant article(s) from {source['name']}")
            print(f"  Preview: {news_text[:200]}...")
            break

        except Exception as e:
            print(f"  {source['name']} failed: {e}")
            continue

    if not news_text:
        print("All sources failed. No price adjustment will be applied this run.")
        return result

    decrease_words = ["rollback", "decrease", "down", "cut", "slash", "lower", "drop", "reduce", "fell", "fall"]
    increase_words = ["hike", "increase", "up", "rise", "surge", "jump", "climb", "higher"]

    def extract_adjustment(fuel_keywords_list, text):
        fuel_pattern = "|".join(fuel_keywords_list)
        price_token  = r"(?:php|p|peso)?\s*(\d+\.?\d*)"
        action_token = r"(?:rollback|hike|increase|decrease|cut|drop|rise|down|up)"

        patterns = [
            rf"(?:{fuel_pattern}).{{0,60}}?{action_token}.{{0,30}}?{price_token}",
            rf"{price_token}.{{0,30}}?{action_token}.{{0,40}}?(?:{fuel_pattern})",
            rf"(?:{fuel_pattern}).{{0,20}}?(?:down|up).{{0,10}}?{price_token}",
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    amount = float(match.group(1))
                    if amount > MAX_SAFE_ADJUSTMENT:
                        print(f"  ⚠️  Skipping amount {amount} — exceeds safety cap of {MAX_SAFE_ADJUSTMENT}. "
                        f"Context: ...{text[max(0,match.start()-40):match.end()+40]}...")
                    continue
                    context_start = max(0, match.start() - 60)
                    context_end   = min(len(text), match.end() + 60)
                    context = text[context_start:context_end]
                    is_decrease = any(w in context for w in decrease_words)
                    is_increase = any(w in context for w in increase_words)
                    if not is_decrease and not is_increase:
                        print(f"  Direction unclear for amount {amount}. Skipping.")
                        continue
                    signed = -amount if is_decrease else amount
                    print(f"  Found: {'+' if signed > 0 else ''}{signed:.2f} for [{fuel_pattern}]")
                    return signed
                except ValueError:
                    continue
        return 0.0

    result["gasoline_change"] = extract_adjustment(
        ["gasoline", "gas", "unleaded", "petrol"], news_text
    )
    result["diesel_change"] = extract_adjustment(
        ["diesel"], news_text
    )
    result["kerosene_change"] = extract_adjustment(
        ["kerosene", "gaas"], news_text
    )

    print(f"\nFinal extracted adjustments: {result}")
    return result


def apply_doe_updates(adjustments):
    if all(v == 0.0 for v in adjustments.values()):
        print("\nNo adjustments to apply. Database unchanged.")
        return

    print(f"\n[{datetime.now()}] Fetching all active Verified prices from Supabase...")

    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/prices?status=eq.Verified&select=id,fuel_type,price,station_id",
        headers=SUPABASE_HEADERS
    )
    resp.raise_for_status()
    verified_prices = resp.json()

    print(f"  Found {len(verified_prices)} Verified price rows to process.")

    updated_count = 0
    skipped_count = 0

    for item in verified_prices:
        fuel      = item["fuel_type"].lower()
        old_price = float(item["price"])

        if "diesel" in fuel:
            delta = adjustments["diesel_change"]
        elif "kerosene" in fuel or "gaas" in fuel:
            delta = adjustments["kerosene_change"]
        else:
            delta = adjustments["gasoline_change"]

        if delta == 0.0:
            skipped_count += 1
            continue

        new_price = round(old_price + delta, 2)

        if new_price < 20.0:
            print(f"  Skipping {item['fuel_type']} - computed price P{new_price} is unrealistically low.")
            skipped_count += 1
            continue

        # Step 1: Archive the old row to preserve history
        archive_resp = requests.patch(
            f"{SUPABASE_URL}/rest/v1/prices?id=eq.{item['id']}",
            headers=SUPABASE_HEADERS,
            json={"status": "Archived"}
        )

        if archive_resp.status_code not in (200, 204):
            print(f"  Failed to archive price ID {item['id']}: {archive_resp.text}")
            continue

        # Step 2: Insert fresh Unverified row with old_price preserved
        insert_resp = requests.post(
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

        if insert_resp.status_code in (200, 201):
            direction = "UP  " if delta > 0 else "DOWN"
            print(f"  [{direction}] {item['fuel_type']}: P{old_price} -> P{new_price} (delta {'+' if delta > 0 else ''}{delta})")
            updated_count += 1
        else:
            print(f"  Failed to insert new price for {item['fuel_type']}: {insert_resp.text}")

    print(f"\nDone! {updated_count} prices updated, {skipped_count} skipped.")


if __name__ == "__main__":
    print("=" * 60)
    print("  Benguet Gas Monitor - DOE Auto-Sync Script")
    print(f"  Run time: {datetime.now().strftime('%A, %B %d %Y %I:%M %p')}")
    print("=" * 60)

    if not SUPABASE_URL or not SUPABASE_KEY:
        raise EnvironmentError(
            "SUPABASE_URL or SUPABASE_KEY environment variables are not set!\n"
            "Set them as GitHub Secrets or in a local .env file for testing."
        )

    latest_advisory = scrape_doe_advisory()
    apply_doe_updates(latest_advisory)