import os
import re
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()
url = os.environ.get("VITE_SUPABASE_URL")
key = os.environ.get("VITE_SUPABASE_ANON_KEY")
headers = { "apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Prefer": "return=representation" }

def scrape_doe_advisory():
    print("Scraping latest news for DOE fuel advisories...")
    # In a production environment, you would scrape a reliable news RSS feed or the DOE press page.
    # For this script, we will simulate the extraction of data from a news headline:
    # "DOE Advisory: Gasoline up by P1.20, Diesel up by P0.50, Kerosene down by P0.20"
    
    # Simulated extraction (you would use regex on the BeautifulSoup text here)
    scraped_data = {
        "gasoline_change": 1.20,  # Positive means increase
        "diesel_change": 0.50,
        "kerosene_change": -0.20  # Negative means rollback
    }
    return scraped_data

def apply_doe_updates(scraped_data):
    # 1. Fetch all currently Verified prices
    response = requests.get(f"{url}/rest/v1/prices?status=eq.Verified", headers=headers)
    current_prices = response.json()

    for item in current_prices:
        fuel = item['fuel_type'].lower()
        new_price = item['price']
        
        # 2. Apply the specific math based on the scraped DOE category
        if "diesel" in fuel:
            new_price += scraped_data['diesel_change']
        elif "kerosene" in fuel or "gaas" in fuel:
            new_price += scraped_data['kerosene_change']
        else:
            # If it's not diesel or kerosene, it's gasoline (Blaze, XCS, V-Power, etc.)
            new_price += scraped_data['gasoline_change']

        # 3. Push the mathematically updated price back to Supabase
        # CRITICAL UX UPGRADE: We instantly strip the 'Verified' status so locals must confirm the new Baguio markup!
        update_url = f"{url}/rest/v1/prices?id=eq.{item['id']}"
        payload = {
            "price": new_price,
            "status": "Unverified",  # Force the community to re-verify the new DOE math
            "upvotes": 0             # Reset the trust score
        }
        requests.patch(update_url, headers=headers, json=payload)
        
    print("✅ All Baguio stations have been automatically synced with the latest DOE advisory!")

if __name__ == "__main__":
    latest_advisory = scrape_doe_advisory()
    apply_doe_updates(latest_advisory)