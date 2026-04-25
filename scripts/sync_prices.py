import os
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from fuel_categories import (
    FUEL_CATEGORY_MAP,
    DOE_CATEGORY_KEYWORDS,
    DOE_CATEGORY_MATCH_ORDER,
    CATEGORY_LABELS,
)


# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

DRY_RUN = os.environ.get("DRY_RUN", "false").strip().lower() == "true"
DEBUG   = os.environ.get("DEBUG",   "false").strip().lower() == "true"


def _sanitize_url(raw):
    if not raw:
        return raw
    raw = raw.strip().strip('"').strip("'")
    md_match = re.search(r"\(https?://[^)]+\)", raw)
    if md_match:
        raw = md_match.group(0).strip("()")
    raw = re.sub(r"^\[|\]$", "", raw.strip())
    raw = raw.rstrip("/")
    return raw


SUPABASE_URL = _sanitize_url(os.environ.get("SUPABASE_URL"))
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()

SUPABASE_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}

MAX_SAFE_ADJUSTMENT = 30.0


# ---------------------------------------------------------------------------
# SCRAPER
# ---------------------------------------------------------------------------

def scrape_doe_advisory() -> dict:
    """
    Scrape DOE advisory news and return a per-category adjustment dict:
    {
        "gasoline_91":     float,   # signed delta in PHP/L
        "gasoline_95":     float,
        "gasoline_97plus": float,
        "diesel":          float,
        "premium_diesel":  float,
        "kerosene":        float,
    }
    """
    print(f"[{datetime.now()}] Starting DOE advisory scrape...")

    sources = [
        {"name": "Google News RSS", "url": "https://news.google.com/rss/search?q=fuel+price+rollback+philippines+diesel+gasoline+when:7d&hl=en-PH&gl=PH&ceid=PH:en"},
        {"name": "GMA News RSS",    "url": "https://data.gmanetwork.com/gno/rss/money/feed.xml"},
        {"name": "PhilStar RSS",    "url": "https://www.philstar.com/rss/business"},
    ]

    scrape_headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    fuel_keywords  = ["fuel", "oil price", "gasoline", "diesel", "petrol", "pump price", "rollback", "hike"]
    raw_news_text  = ""

    for source in sources:
        try:
            print(f"  Trying: {source['name']}...")
            resp = requests.get(source["url"], headers=scrape_headers, timeout=15)
            resp.raise_for_status()

            soup  = BeautifulSoup(resp.content, "lxml-xml")
            items = soup.find_all("item")[:10]
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

            clean_preview = BeautifulSoup(raw_news_text, "html.parser").get_text()
            print(f"  --- CLEANED TEXT (first 500 chars) ---")
            print(f"  {clean_preview[:500]}")
            print(f"  --- END CLEANED TEXT ---")
            break

        except Exception as e:
            print(f"  {source['name']} failed: {e}")
            continue

    if not raw_news_text:
        print("All sources failed. Database unchanged.")
        return {cat: 0.0 for cat in DOE_CATEGORY_MATCH_ORDER}

    adjustments = _extract_all_category_adjustments(raw_news_text)
    print(f"\nExtracted adjustments:")
    for cat in DOE_CATEGORY_MATCH_ORDER:
        label = CATEGORY_LABELS.get(cat, cat)
        val   = adjustments[cat]
        flag  = "  ← inherited" if adjustments.get(f"_{cat}_inherited") else ""
        print(f"  {label:<40}: {val:+.2f} PHP/L{flag}")
    return adjustments


# ---------------------------------------------------------------------------
# EXTRACTION — 6 DOE categories
# ---------------------------------------------------------------------------

def _extract_all_category_adjustments(raw_text: str) -> dict:
    """
    Extract a signed price adjustment for each of the 6 DOE categories.

    Disambiguation strategy:
    - Process in DOE_CATEGORY_MATCH_ORDER (premium_diesel before diesel).
    - For the plain "diesel" category, scrub "premium diesel"/"diesel plus" from
      the search text first so that keyword won't cross-match.
    - Fallback inheritance (each marked with _<cat>_inherited=True for logging):
        * gasoline_91/95/97+ → if all zero, apply generic "gasoline" match
        * gasoline_97plus    → if still zero, inherit from gasoline_95
        * premium_diesel     → if zero, inherit from diesel (same adjustment is common)
        * kerosene           → NOT auto-inherited (DOE announces it separately)
    """
    clean_text = BeautifulSoup(raw_text, "html.parser").get_text().lower()

    # Create a diesel-safe copy that masks premium-diesel phrases so plain
    # "diesel" matching won't accidentally grab a "diesel plus" value.
    diesel_safe_text = re.sub(
        r"(?:premium\s+diesel|diesel\s+plus|diesel\+|special\s+diesel)",
        "___prem_diesel___",
        clean_text,
    )

    adjustments = {}
    for category in DOE_CATEGORY_MATCH_ORDER:
        keywords    = DOE_CATEGORY_KEYWORDS[category]
        search_text = diesel_safe_text if category == "diesel" else clean_text
        adjustments[category] = _extract_adjustment(keywords, search_text, category)

    # ── Fallback 1: generic gasoline → all 3 gasoline grades ──────────────
    all_gas_zero = all(adjustments[c] == 0.0 for c in ["gasoline_91", "gasoline_95", "gasoline_97plus"])
    if all_gas_zero:
        generic_gas = _extract_adjustment(
            ["gasoline", "gas price", "petrol", "oil price"], clean_text, "generic_gasoline"
        )
        if generic_gas != 0.0:
            for gas_cat in ["gasoline_91", "gasoline_95", "gasoline_97plus"]:
                adjustments[gas_cat] = generic_gas
                adjustments[f"_{gas_cat}_inherited"] = True
    else:
        # ── Fallback 2: inherit gasoline_97plus from gasoline_95 ──────────
        if adjustments["gasoline_97plus"] == 0.0 and adjustments["gasoline_95"] != 0.0:
            adjustments["gasoline_97plus"] = adjustments["gasoline_95"]
            adjustments["_gasoline_97plus_inherited"] = True

    # ── Fallback 3: premium_diesel inherits from diesel ───────────────────
    if adjustments["premium_diesel"] == 0.0 and adjustments["diesel"] != 0.0:
        adjustments["premium_diesel"] = adjustments["diesel"]
        adjustments["_premium_diesel_inherited"] = True

    return adjustments


def _extract_adjustment(fuel_keywords_list: list, raw_text: str, category: str = "") -> float:
    """
    Given a list of fuel keywords and scraped text, return a signed price delta.
    Positive = price hike. Negative = price rollback/decrease. 0.0 = not found.
    """
    clean_text = BeautifulSoup(raw_text, "html.parser").get_text().lower()         if "<" in raw_text else raw_text.lower()

    fuel_pattern   = "|".join(re.escape(kw) for kw in fuel_keywords_list)
    price_token    = r"(?:php|p|₱|peso)?\s*(\d+\.?\d*)"
    action_token   = r"(?:rollback|hike|increase|decrease|cut|drop|rise|down|up)"
    decrease_words = ["rollback", "decrease", "down", "cut", "slash", "lower",
                      "drop", "reduce", "fell", "fall"]
    increase_words = ["hike", "increase", "up", "rise", "surge", "jump",
                      "climb", "higher"]

    patterns = [
        # Pattern 1: fuel -> action -> price   e.g. "diesel rollback of P1.50"
        rf"(?:{fuel_pattern}).{{0,60}}?{action_token}.{{0,30}}?{price_token}",
        # Pattern 2: price -> action -> fuel   e.g. "P1.50 rollback for diesel"
        rf"{price_token}.{{0,30}}?{action_token}.{{0,40}}?(?:{fuel_pattern})",
        # Pattern 3: fuel -> down/up -> price  e.g. "diesel: down P1.50"
        rf"(?:{fuel_pattern}).{{0,20}}?(?:down|up).{{0,10}}?{price_token}",
        # Pattern 4: action -> price -> fuel   e.g. "rollback of P3.41 for gasoline"
        rf"{action_token}.{{0,20}}?{price_token}.{{0,20}}?(?:{fuel_pattern})",
    ]

    for i, pattern in enumerate(patterns, 1):
        match = re.search(pattern, clean_text)
        if match:
            try:
                amount = float(match.group(1))
                if amount > MAX_SAFE_ADJUSTMENT:
                    ctx = clean_text[max(0, match.start() - 40):match.end() + 40]
                    print(f"  Skipping {amount} — exceeds cap ({MAX_SAFE_ADJUSTMENT}). Context: ...{ctx}...")
                    continue

                ctx_start = max(0, match.start() - 60)
                ctx_end   = min(len(clean_text), match.end() + 60)
                context   = clean_text[ctx_start:ctx_end]

                is_decrease = any(w in context for w in decrease_words)
                is_increase = any(w in context for w in increase_words)

                if not is_decrease and not is_increase:
                    print(f"  [Pattern {i}] ({category}) Matched {amount} but direction unclear. "
                          f"Context: ...{context}...")
                    continue

                signed = -amount if is_decrease else amount
                print(f"  [Pattern {i}] ({category}) Found {'+' if signed > 0 else ''}{signed:.2f}")
                return signed

            except ValueError:
                continue
        else:
            if DEBUG:
                print(f"  [Pattern {i}] ({category}) No match for [{fuel_pattern}]")

    if DEBUG:
        print(f"  ({category}) No adjustment found.")
    return 0.0


# ---------------------------------------------------------------------------
# SUPABASE UPDATER
# ---------------------------------------------------------------------------

def _build_fuel_delta_map(adjustments: dict) -> dict:
    """
    Build a flat { fuel_type_string: delta } lookup from the 6-category adjustments
    using FUEL_CATEGORY_MAP as the source of truth.

    The DOE_CATEGORY_MATCH_ORDER determines precedence: if a fuel type somehow
    appears in two categories (it shouldn't), the first category wins.
    """
    fuel_delta_map = {}
    for category in DOE_CATEGORY_MATCH_ORDER:
        delta = adjustments.get(category, 0.0)
        if delta == 0.0:
            continue
        for brand_fuels in FUEL_CATEGORY_MAP.get(category, {}).values():
            for fuel_type in brand_fuels:
                if fuel_type not in fuel_delta_map:
                    fuel_delta_map[fuel_type] = delta
    return fuel_delta_map


def apply_doe_updates(adjustments: dict):
    prefix = "[DRY RUN] " if DRY_RUN else ""

    fuel_delta_map = _build_fuel_delta_map(adjustments)

    if not fuel_delta_map:
        print(f"\n{prefix}No adjustments to apply. Database unchanged.")
        return

    print(f"\n[{datetime.now()}] {prefix}Fetching Verified prices from Supabase...")
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/prices"
        f"?status=eq.Verified&select=id,fuel_type,price,station_id",
        headers=SUPABASE_HEADERS,
    )
    resp.raise_for_status()
    verified = resp.json()
    print(f"  Found {len(verified)} Verified rows.")

    if DRY_RUN:
        _dry_run_preview(verified, adjustments, fuel_delta_map)
        return

    updated = skipped = 0
    for item in verified:
        fuel_type = item["fuel_type"]
        delta     = fuel_delta_map.get(fuel_type)

        if delta is None:
            if DEBUG:
                print(f"  Skipping unmapped fuel type: '{fuel_type}'")
            skipped += 1
            continue

        old_price = float(item["price"])
        new_price = round(old_price + delta, 2)

        if new_price < 20.0:
            print(f"  Skipping {fuel_type} — computed ₱{new_price} is unrealistically low.")
            skipped += 1
            continue

        archive = requests.patch(
            f"{SUPABASE_URL}/rest/v1/prices?id=eq.{item['id']}",
            headers=SUPABASE_HEADERS,
            json={"status": "Archived"},
        )
        if archive.status_code not in (200, 204):
            print(f"  Failed to archive ID {item['id']}: {archive.text}")
            continue

        insert = requests.post(
            f"{SUPABASE_URL}/rest/v1/prices",
            headers=SUPABASE_HEADERS,
            json={
                "station_id": item["station_id"],
                "fuel_type":  fuel_type,
                "price":      new_price,
                "old_price":  old_price,
                "status":     "Unverified",
                "upvotes":    0,
            },
        )
        if insert.status_code in (200, 201):
            direction = "UP  " if delta > 0 else "DOWN"
            print(f"  [{direction}] {fuel_type}: ₱{old_price} → ₱{new_price} ({delta:+.2f})")
            updated += 1
        else:
            print(f"  Insert failed for {fuel_type}: {insert.text}")

    print(f"\nDone. {updated} updated, {skipped} skipped.")


# ---------------------------------------------------------------------------
# DRY RUN PREVIEW
# ---------------------------------------------------------------------------

def _dry_run_preview(verified: list, adjustments: dict, fuel_delta_map: dict):
    print("\n" + "=" * 65)
    print("  DRY RUN PREVIEW — no database changes will be made")
    print("=" * 65)

    print("  Adjustments by DOE category:")
    for cat in DOE_CATEGORY_MATCH_ORDER:
        val      = adjustments.get(cat, 0.0)
        label    = CATEGORY_LABELS.get(cat, cat)
        note     = " (inherited)" if adjustments.get(f"_{cat}_inherited") else ""
        arrow    = "▲ HIKE" if val > 0 else ("▼ ROLLBACK" if val < 0 else "— no change")
        print(f"    {label:<40}: {val:+.2f} PHP/L  {arrow}{note}")
    print()

    would_update = []
    would_skip   = []
    would_guard  = []
    unmapped     = []

    for item in verified:
        fuel_type = item["fuel_type"]
        old_price = float(item["price"])
        delta     = fuel_delta_map.get(fuel_type)

        if delta is None:
            unmapped.append(fuel_type)
            continue

        if delta == 0.0:
            would_skip.append(fuel_type)
            continue

        new_price = round(old_price + delta, 2)
        if new_price < 20.0:
            would_guard.append((fuel_type, old_price, new_price))
            continue

        would_update.append((fuel_type, old_price, new_price, delta))

    if would_update:
        print(f"  WOULD UPDATE ({len(would_update)} rows):")
        print(f"  {'Fuel Type':<30} {'Old':>8}  {'New':>8}  {'Δ':>8}  Direction")
        print(f"  {'-'*30} {'-'*8}  {'-'*8}  {'-'*8}  ---------")
        for fuel_type, old, new, d in sorted(would_update):
            direction = "▲ HIKE" if d > 0 else "▼ ROLLBACK"
            print(f"  {fuel_type:<30}  ₱{old:>6.2f}  ₱{new:>6.2f}  {d:>+7.2f}  {direction}")

    if would_guard:
        print(f"\n  WOULD SKIP — price floor triggered ({len(would_guard)} rows):")
        for ft, old, new in would_guard:
            print(f"    {ft}: ₱{old:.2f} → ₱{new:.2f} (below ₱20 floor)")

    if would_skip:
        print(f"\n  WOULD SKIP — delta is zero for this category ({len(would_skip)} rows):")
        for ft in sorted(set(would_skip)):
            print(f"    {ft}")

    if unmapped:
        unique_unmapped = sorted(set(unmapped))
        print(f"\n  ⚠️  UNMAPPED fuel types — not in fuel_categories.py ({len(unique_unmapped)} unique):")
        print(f"  Add these to fuel_categories.py to include them in future syncs.")
        for ft in unique_unmapped:
            print(f"    {ft}")

    print()
    print(f"  Summary: {len(would_update)} would update | "
          f"{len(would_skip) + len(would_guard)} would skip | "
          f"{len(set(unmapped))} unmapped")
    print("=" * 65)
    print("  To apply for real, re-run with DRY_RUN=false")
    print("=" * 65)


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 65)
    print("  Benguet Gas Monitor — DOE Auto-Sync Script")
    print(f"  Run time : {datetime.now().strftime('%A, %B %d %Y %I:%M %p')}")
    print(f"  Mode     : {'DRY RUN (no DB writes)' if DRY_RUN else 'LIVE (will write to DB)'}")
    print("=" * 65)

    if not SUPABASE_URL or not SUPABASE_KEY:
        raise EnvironmentError(
            "SUPABASE_URL or SUPABASE_KEY not set.\n"
            "Add them as GitHub Secrets or in a local .env file."
        )

    apply_doe_updates(scrape_doe_advisory())