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
    print("Fetching real-time news for DOE fuel advisories...")
    
    # Target Google News RSS for PH oil price updates from the last 3 days
    url = "https://news.google.com/rss/search?q=oil+price+update+philippines+gasoline+diesel+when:3d"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    scraped_data = {"gasoline_change": 0.0, "diesel_change": 0.0, "kerosene_change": 0.0}
    
    try:
        response = requests.get(url, headers=headers)
        # Parse the RSS XML feed using BeautifulSoup
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Combine the titles and descriptions of the top 3 news articles into one big text block
        items = soup.find_all('item')[:3]
        if not items:
            print("No recent fuel news found this week. Skipping update.")
            return scraped_data
            
        news_text = " ".join([item.title.text + " " + item.description.text for item in items]).lower()
        print(f"Analyzing news headlines: {news_text[:150]}...")

        # Define keywords that indicate whether the price is going up or down
        decrease_words = ['rollback', 'decrease', 'down', 'cut', 'slash', 'lower']
        
        def extract_price(fuel_name, text):
            # Regex 1: Looks for "gasoline hike of P1.20"
            pattern1 = rf"{fuel_name}.{{0,40}}?(?:rollback|hike|increase|decrease|down|up|cut|slash).*?(?:php|p|₱)?\s*(\d+\.\d+)"
            # Regex 2: Looks for "P1.20 hike in gasoline"
            pattern2 = rf"(?:php|p|₱)?\s*(\d+\.\d+).{{0,40}}?(?:rollback|hike|increase|decrease|down|up|cut|slash).{{0,40}}?{fuel_name}"
            
            match = re.search(pattern1, text) or re.search(pattern2, text)
            
            if match:
                amount = float(match.group(1))
                # Check the surrounding 40 characters to see if it mentions a rollback/cut
                context = text[max(0, match.start()-40) : min(len(text), match.end()+40)]
                is_decrease = any(w in context for w in decrease_words)
                
                return -amount if is_decrease else amount
            return 0.0

        scraped_data["gasoline_change"] = extract_price("gasoline", news_text) or extract_price("gas", news_text)
        scraped_data["diesel_change"] = extract_price("diesel", news_text)
        scraped_data["kerosene_change"] = extract_price("kerosene", news_text)
        
        print(f"Extracted mathematical adjustments: {scraped_data}")
        return scraped_data
        
    except Exception as e:
        print(f"Web scraping failed: {e}")
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