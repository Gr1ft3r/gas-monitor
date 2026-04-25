# fuel_categories.py
# ─────────────────────────────────────────────────────────────────────────────
# DOE FUEL CATEGORY → BRAND-SPECIFIC FUEL TYPE MAPPING
#
# The Philippine DOE weekly price bulletin reports adjustments in 6 categories:
#   1. gasoline_91    — Gasoline RON 91 (Unleaded Regular)
#   2. gasoline_95    — Gasoline RON 95 (Premium)
#   3. gasoline_97plus— Gasoline RON 97/98/100 (Super Premium)
#   4. diesel         — Diesel (Regular, Cetane ~50)
#   5. premium_diesel — Diesel Plus / Premium Diesel (Cetane 54-55)
#   6. kerosene       — Kerosene
#
# Each key maps to a dict of { "Brand": ["Exact fuel_type string in DB", ...] }
# The fuel_type strings must EXACTLY match what is stored in the `prices` table.
#
# ⚠️  PETRON NOTE (verified April 2026):
#   Petron has reformulated their gasoline lineup. Their current 3-grade lineup is:
#     Blaze 100 (RON 100) → gasoline_97plus
#     XCS       (RON 95)  → gasoline_95       [was 97 in older formulations]
#     Xtra Advance (RON 91) → gasoline_91     [was 93 RON — now officially 91]
#   "Super Xtra 91" appears discontinued. If your DB still has "Super Xtra 91"
#   entries, include it under gasoline_91 — it will receive the same adjustment.
#
# ⚠️  BIODIESEL NOTE:
#   Flying V Biodiesel and Phoenix Biodiesel are diesel-blend products (B2/B5).
#   They follow the standard Diesel (gasoline_diesel) price adjustment.
#
# ⚠️  LPG/AUTOGAS NOTE:
#   Phoenix Autogas (LPG) is NOT covered by DOE liquid fuel weekly bulletins.
#   It is intentionally excluded from this map — do not apply diesel/gas adjustments.
# ─────────────────────────────────────────────────────────────────────────────

FUEL_CATEGORY_MAP = {

    # ── RON 91 ── Regular Unleaded Gasoline ──────────────────────────────────
    "gasoline_91": {
        "Petron":       ["Xtra Advance 93", "Super Xtra 91"],  # Both now 91 RON per Petron
        "Shell":        ["FuelSave Unleaded 91"],
        "Caltex":       ["Silver 91 with Techron"],
        "Cleanfuel":    ["Clean 91"],
        "Flying V":     ["Unleaded 91"],
        "SeaOil":       ["Extreme U 91"],
        "Total":        ["Premier 91"],
        "Phoenix":      ["Super Regular 91"],
        "Unioil":       ["Unleaded 91"],
        "Independent":  ["Unleaded 91"],
    },

    # ── RON 95 ── Premium Gasoline ────────────────────────────────────────────
    "gasoline_95": {
        "Petron":       ["XCS 95"],
        "Shell":        ["V-Power Gasoline 95", "FuelSave 95"],
        "Caltex":       ["Platinum 95 with Techron"],
        "Cleanfuel":    ["Premium 95"],
        "Flying V":     ["Gasoline 95"],
        "SeaOil":       ["Extreme 95"],
        "Total":        ["Excellium 95"],
        "Phoenix":      ["Premium 95"],
        "Unioil":       ["Premium 95"],
        "Independent":  ["Premium 95"],
    },

    # ── RON 97/98/100 ── Super Premium Gasoline ───────────────────────────────
    # Shell PH and Caltex PH do NOT carry an RON 97+ product.
    "gasoline_97plus": {
        "Petron":       ["Blaze 100"],
        "SeaOil":       ["Extreme 97"],
        "Phoenix":      ["Premium 98"],
        "Unioil":       ["Premium 97"],
    },

    # ── Diesel ── Regular Diesel (Cetane ~50) ─────────────────────────────────
    "diesel": {
        "Petron":       ["Diesel Max"],
        "Shell":        ["FuelSave Diesel"],
        "Caltex":       ["Diesel with Techron D"],
        "Cleanfuel":    ["Diesel"],
        "Flying V":     ["Biodiesel"],       # B2/B5 blend — follows diesel adjustment
        "SeaOil":       ["Exceed Diesel"],   # Cetane 51-52, standard category
        "Total":        ["Standard Diesel"],
        "Phoenix":      ["Biodiesel"],       # B2 blend — follows diesel adjustment
        "Unioil":       ["Euro 5 Diesel"],
        "Independent":  ["Diesel"],
    },

    # ── Premium Diesel ── Diesel Plus (Cetane 54-55+) ─────────────────────────
    # Cleanfuel, Flying V, Phoenix, Unioil, and Independent do NOT carry
    # a separate premium diesel product.
    "premium_diesel": {
        "Petron":       ["Turbo Diesel"],           # Cetane 55
        "Shell":        ["V-Power Diesel"],          # Cetane 54
        "Caltex":       ["Power Diesel with Techron D"],  # Cetane 54+
        "Total":        ["Excellium Diesel"],        # Cetane 54
    },

    # ── Kerosene ──────────────────────────────────────────────────────────────
    "kerosene": {
        "Petron":       ["Kerosene"],
        "Shell":        ["Kerosene"],
        "Caltex":       ["Kerosene"],
        "SeaOil":       ["Kerosene"],
        "Independent":  ["Kerosene"],
    },

}

# ─────────────────────────────────────────────────────────────────────────────
# REVERSE LOOKUP: fuel_type string → DOE category
# e.g., get_doe_category("Turbo Diesel") → "premium_diesel"
# Returns None for uncategorized fuels (e.g., Autogas/LPG).
# ─────────────────────────────────────────────────────────────────────────────
_REVERSE_MAP = {}
for _category, _brands in FUEL_CATEGORY_MAP.items():
    for _brand, _fuels in _brands.items():
        for _fuel in _fuels:
            _REVERSE_MAP[_fuel.lower()] = _category


def get_doe_category(fuel_type: str) -> str | None:
    """Return the DOE category key for a given fuel_type string, or None if not mapped."""
    return _REVERSE_MAP.get(fuel_type.strip().lower())


def get_brand_fuels_for_category(category: str, brand: str) -> list[str]:
    """Return the list of fuel_type strings for a given DOE category and brand."""
    return FUEL_CATEGORY_MAP.get(category, {}).get(brand, [])


def get_all_fuels_for_category(category: str) -> dict[str, list[str]]:
    """Return the full {brand: [fuel_types]} dict for a DOE category."""
    return FUEL_CATEGORY_MAP.get(category, {})


# ─────────────────────────────────────────────────────────────────────────────
# DOE SCRAPER KEYWORDS
# These are the text patterns the scraper looks for in DOE news/advisories.
# Each list entry is a lowercase substring to match against the scraped text.
# ─────────────────────────────────────────────────────────────────────────────
DOE_CATEGORY_KEYWORDS = {
    "gasoline_91": [
        "ron 91", "unleaded 91", "regular gasoline", "unleaded gasoline",
        "gasoline (ron 91)", "gasoline ron 91",
    ],
    "gasoline_95": [
        "ron 95", "premium 95", "premium gasoline",
        "gasoline (ron 95)", "gasoline ron 95",
    ],
    "gasoline_97plus": [
        "ron 97", "ron 98", "ron 100", "premium plus gasoline",
        "super premium", "gasoline (ron 97", "gasoline ron 97",
    ],
    "diesel": [
        "diesel",     # Note: must be checked AFTER "premium diesel" / "diesel plus"
        "regular diesel", "biodiesel",
    ],
    "premium_diesel": [
        "diesel plus", "premium diesel", "special diesel",
        "diesel+",
    ],
    "kerosene": [
        "kerosene",
    ],
}
# ⚠️  Matching order matters for diesel vs premium diesel.
# Always check premium_diesel keywords BEFORE diesel to avoid false matches.
DOE_CATEGORY_MATCH_ORDER = [
    "gasoline_97plus",
    "gasoline_95",
    "gasoline_91",
    "premium_diesel",   # ← must come before "diesel"
    "diesel",
    "kerosene",
]


# ─────────────────────────────────────────────────────────────────────────────
# HUMAN-READABLE LABELS (for logs and alert messages)
# ─────────────────────────────────────────────────────────────────────────────
CATEGORY_LABELS = {
    "gasoline_91":     "Gasoline RON 91 (Unleaded)",
    "gasoline_95":     "Gasoline RON 95 (Premium)",
    "gasoline_97plus": "Gasoline RON 97/98/100 (Super Premium)",
    "diesel":          "Diesel (Regular)",
    "premium_diesel":  "Diesel Plus (Premium)",
    "kerosene":        "Kerosene",
}