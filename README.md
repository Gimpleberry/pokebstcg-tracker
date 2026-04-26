# 🃏 TCG Drop Radar — Restock Tracker

Monitor Target, Walmart, and Best Buy for TCG bundles, booster boxes, and assortments.
Get instant alerts the moment a product drops back into stock.

---

## Quick Start

### 1. Install dependencies
```bash
pip install requests beautifulsoup4 schedule lxml
# Optional (SMS alerts):
pip install twilio
```

### 2. Add your products to track
Edit the `PRODUCTS` list in `tracker.py`:
```python
PRODUCTS = [
    {
        "name": "Pokemon Scarlet ETB",
        "retailer": "target",       # target | walmart | bestbuy
        "url": "https://www.target.com/p/...",
        "sku": "88907490",          # from the URL or product page
    },
    {
        "name": "One Piece OP-09 Box",
        "retailer": "walmart",
        "url": "https://www.walmart.com/ip/12345678",
        "item_id": "12345678",      # number in the Walmart URL
    },
    {
        "name": "Lorcana Booster Box",
        "retailer": "bestbuy",
        "url": "https://www.bestbuy.com/site/product/1234567.p",
        "sku": "1234567",           # number before .p in Best Buy URL
    },
]
```

### 3. Configure notifications

#### Option A — Push Notification (Free, easiest)
1. Go to https://ntfy.sh
2. Subscribe to a topic name you make up (keep it private)
3. Download the ntfy app on your phone and subscribe to the same topic
4. Set in `tracker.py`:
```python
"notify_push": True,
"ntfy_topic": "your-secret-topic-name-123",
```

#### Option B — Email
1. Enable "App Passwords" in your Gmail account settings
2. Set in `tracker.py`:
```python
"notify_email": True,
"email_sender": "you@gmail.com",
"email_password": "your-app-password",
"email_recipient": "you@gmail.com",
```

#### Option C — SMS via Twilio
1. Sign up at twilio.com (free trial available)
2. Set in `tracker.py`:
```python
"notify_sms": True,
"twilio_account_sid": "ACxxxx...",
"twilio_auth_token": "your_token",
"twilio_from": "+1XXXXXXXXXX",
"twilio_to": "+1XXXXXXXXXX",
```

### 4. Run the tracker
```bash
python tracker.py
```

### 5. Open the dashboard
Open `dashboard.html` in your browser for a visual overview.

---

## How to Find SKUs / Item IDs

| Retailer | Where to find |
|---|---|
| **Target** | Last digits in the URL: `.../A-88907490` → SKU is `88907490` |
| **Walmart** | Number in URL: `walmart.com/ip/12345678` → Item ID is `12345678` |
| **Best Buy** | Number before `.p`: `bestbuy.com/site/product/1234567.p` → SKU is `1234567` |

---

## Run 24/7 (Optional)

### On a Raspberry Pi or always-on PC:
```bash
# Run in background with nohup
nohup python tracker.py &

# Or use screen
screen -S tcg-tracker
python tracker.py
# Ctrl+A then D to detach
```

### Free cloud hosting:
- **Railway.app** — Deploy free, runs 24/7
- **Render.com** — Free background worker tier
- **PythonAnywhere** — Free tier with scheduled tasks

---

## TCG Products Worth Tracking

| Game | What to track |
|---|---|
| **Pokémon** | Elite Trainer Boxes, Booster Bundles, Booster Boxes |
| **One Piece** | Booster Boxes (OP-07, OP-08, OP-09) |
| **Yu-Gi-Oh** | Booster Boxes, Special Editions |
| **Magic: The Gathering** | Bundles, Set Boosters, Draft Boosters |
| **Lorcana** | Booster Boxes, Illumineer's Trove |
| **Digimon** | Booster Boxes |
| **Dragon Ball Super** | Booster Boxes, Special Sets |

---

## Notes
- Default check interval: every 5 minutes (respectful to retailers)
- Alerts fire only when a product transitions from OUT → IN STOCK
- `restock_history.json` saves previous state between restarts
- `status_snapshot.json` is used by the dashboard for live display
