# ⛽ Benguet Gas Monitor

A hyper-local, community-driven web application designed to track and verify real-time fuel pump prices across Baguio City, La Trinidad, and Tuba. 

## 📖 Overview
While the Philippine Department of Energy (DOE) announces national fuel price adjustments weekly, actual pump prices in the Cordillera region vary wildly due to elevation and logistics costs. This project solves the "Cold Start" API problem by utilizing **community crowdsourcing** with built-in anti-spam mechanics, paired with an **automated Python scraper** that syncs national DOE adjustments directly to local baselines.

## ✨ Key Features
* **Community Verification System:** Users can update prices, report empty pumps, or flag retired fuels. A 3-vote threshold (tracked via localized device fingerprinting) automatically verifies and publishes changes without requiring user accounts.
* **Automated DOE Synchronization:** A Python bot runs via a GitHub Actions cron job every Monday. It scrapes Google News RSS feeds for DOE advisories, extracts the mathematical adjustments (hikes/rollbacks), and automatically updates the database while resetting community verifications.
* **ISP Block Bypass:** Philippine telecom providers (Globe/Smart) frequently block free `*.supabase.co` domains. This app utilizes Vercel API Rewrites to act as a secure reverse proxy, masking the database traffic and guaranteeing 100% uptime on mobile data.
* **Real-Time UI:** Built with React and Tailwind CSS, featuring instant search filtering, dynamic city groupings, and brand categorization.

## 🏗️ Architecture & Tech Stack
* **Frontend:** React.js, Vite, Tailwind CSS
* **Backend / Database:** Supabase (PostgreSQL), REST API
* **Automation:** Python 3 (BeautifulSoup4, Requests, Regex), GitHub Actions
* **Hosting & Proxy:** Vercel (`vercel.json` rewrites)

## 🗄️ Database Schema
The Supabase PostgreSQL database is structured relationally:
* `stations`: Stores branch names, cities, and approval statuses.
* `prices`: Stores individual fuel types per station, current price, status (Verified/Unverified/Archived), upvotes, and timestamps.
* `user_votes`: Logs unique `device_id` + `price_id` to prevent voting spam and maintain data integrity.

## 🚀 Getting Started (Local Development)

### 1. Clone the repository
```bash
git clone https://github.com/yourusername/gas-monitor.git
cd gas-monitor
```

### 2. Install dependencies
```bash
npm install
```

### 3. Set up Environment Variables
Create a `.env` file in the root directory and add your Supabase credentials:
```env
VITE_SUPABASE_URL=https://your-project-id.supabase.co
VITE_SUPABASE_ANON_KEY=your-long-anon-key-here
```

### 4. Run the development server
```bash
npm run dev
```
The app will be available at `http://localhost:5173`. 
*(Note: In development mode, Vite uses the direct Supabase URL. In production, Vercel routes traffic through the `/supabase-api` proxy).*

## 🤖 Running the Python Bot Locally
To test the DOE scraping bot on your local machine before the Monday cron job:
```bash
pip install requests beautifulsoup4 python-dotenv supabase
python scripts/update_prices.py
```

## 📄 License
This project is open-source and available under the MIT License.