# ⛽ Benguet Gas Monitor

A hyper-local, community-driven web application for tracking and verifying real-time fuel pump prices across **Baguio City**, **La Trinidad**, and **Tuba**.

***

## 📖 Overview

While the Philippine Department of Energy (DOE) announces national fuel price adjustments weekly, actual pump prices in the Cordillera region vary significantly due to elevation and logistics costs. This project solves the "Cold Start" data problem by combining **community crowdsourcing** with an **automated Python scraper** that syncs national DOE adjustments directly to local baselines — no user accounts required.

***

## ✨ Key Features

### 👍 Community Verification System
Users can confirm prices, update them, or report issues directly from the UI. A **3-vote threshold** (tracked via browser fingerprinting + `localStorage`) automatically verifies and publishes changes. No login required — anti-spam is enforced via composite device IDs.

### 🤖 Automated DOE Synchronization
A Python bot runs via a **GitHub Actions cron job every Monday**. It scrapes Google News RSS feeds for DOE advisories, extracts price adjustments (hikes/rollbacks), and automatically updates the database while resetting community verifications for the new price cycle.

### 🚩 Crowdsourced Reporting
- **🚩 Empty** — Report a fuel as out of stock. At 3 votes, the fuel is flagged automatically.
- **🗑️ Not Sold** — Short-tap to vote that a fuel type is no longer carried (3 votes archives it). **Long-press (800ms)** opens an admin PIN prompt for hard-deletion of a fuel or an entire station.

### 📡 ISP Block Bypass
Philippine telecom providers (Globe/Smart) frequently block free `*.supabase.co` domains. This app uses **Vercel API Rewrites** (`vercel.json`) as a secure reverse proxy, masking database traffic to guarantee connectivity on mobile data.

### 🔁 Cascade Pricing
When a Super Admin verifies a price update, the same price automatically propagates to **all other stations of the same brand in the same city** — keeping data consistent without manual entry for every branch. Kerosene is excluded (pricing varies per station).

### ⚡ Real-Time UI
Built with React and Tailwind CSS. Features instant search filtering, dynamic city groupings, brand tabs, and collapsible station cards optimized for mobile.

***

## 🏗️ Architecture & Tech Stack

| Layer | Technology |
|---|---|
| **Frontend** | React.js, Vite, Tailwind CSS |
| **Database** | Supabase (PostgreSQL), REST API |
| **Automation** | Python 3, BeautifulSoup4, Requests, GitHub Actions |
| **Hosting & Proxy** | Vercel (`vercel.json` rewrites) |

***

## 🗄️ Database Schema

| Table | Description |
|---|---|
| `stations` | Branch names, cities, and approval statuses (`Active`, `Unverified`, `Archived`, `Pending_Admin`) |
| `prices` | Fuel types per station, current price, `old_price`, status (`Verified` / `Unverified` / `Archived`), upvotes, `out_of_stock_votes`, `retired_votes`, and timestamps |
| `user_votes` | Logs unique `device_id` + `price_id` + `vote_type` to prevent voting spam |

***

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
Create a `.env` file in the root directory:
```env
VITE_SUPABASE_URL=https://your-project-id.supabase.co
VITE_SUPABASE_ANON_KEY=your-long-anon-key-here
```

### 4. Run the development server
```bash
npm run dev
```

The app will be available at `http://localhost:5173`.

> **Note:** In development, Vite uses the direct Supabase URL. In production, Vercel routes all database traffic through the `/supabase-api` reverse proxy to bypass ISP blocks.

***

## 🤖 Running the Python Bot Locally

To test the DOE scraping bot before the Monday cron job:
```bash
pip install requests beautifulsoup4 python-dotenv supabase
python scripts/update_prices.py
```

***

## 🔐 Admin Features

The app includes hidden admin capabilities accessible without a separate login:

| Feature | How to Trigger |
|---|---|
| **Super Admin price verify** | Enter a price ending in `*` (e.g. `68.50*`) in the Update modal. Instantly verifies the price, marks the station Active, and cascades to sibling branches. |
| **Admin hard-delete (fuel)** | Long-press (800ms) the 🗑️ **Not Sold** button → enter PIN → select "This Fuel Only" |
| **Admin hard-delete (station)** | Long-press (800ms) the 🗑️ **Not Sold** button → enter PIN → select "Entire Station" |

***

## 📄 License

This project is open-source and available under the **MIT License**.