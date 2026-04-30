import requests
import json
import os
import csv
import io
from datetime import datetime
from urllib.parse import urlparse

# -----------------------------------------------
# Configuration — values come from environment
# -----------------------------------------------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
EXCHANGE_RATE_API_KEY = os.environ.get("EXCHANGE_RATE_API_KEY")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
PRICES_FILE = "last_prices.json"
# -----------------------------------------------


def load_products_from_sheet():
    """Load products from your public Google Sheet (CSV export)."""
    url = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/export?format=csv"
    response = requests.get(url, timeout=15)
    response.raise_for_status()

    products = []
    reader = csv.DictReader(io.StringIO(response.text))
    for row in reader:
        if not row.get("name") or not row.get("url"):
            continue
        products.append({
            "name": row["name"].strip(),
            "url": row["url"].strip(),
            "variant_id": int(row["variant_id"]) if row.get("variant_id", "").strip() else 0,
            "site_type": (row.get("site_type") or "shopify").strip().lower()
        })
    return products


def detect_currency(url):
    """Auto-detect currency from URL: .com.au or .au means AUD, otherwise USD."""
    domain = urlparse(url).netloc.lower()
    if domain.endswith(".com.au") or domain.endswith(".au"):
        return "AUD"
    return "USD"


def get_usd_to_aud_rate():
    """Live USD-to-AUD exchange rate."""
    url = f"https://v6.exchangerate-api.com/v6/{EXCHANGE_RATE_API_KEY}/latest/USD"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json()["conversion_rates"]["AUD"]


def get_shopify_price(url, variant_id):
    """Get the price from a Shopify .json product endpoint."""
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    data = response.json()
    variants = data["product"]["variants"]

    # If no specific variant given, just use the first one
    if variant_id == 0:
        return float(variants[0]["price"])

    # Find the matching variant
    for v in variants:
        if v["id"] == variant_id:
            return float(v["price"])

    # Fall back to first variant if not found
    return float(variants[0]["price"])


def fetch_price_in_aud(product, rate):
    """Fetch a product's current price, converted to AUD if necessary."""
    if product["site_type"] != "shopify":
        return None  # We only support Shopify for now

    raw_price = get_shopify_price(product["url"], product["variant_id"])
    currency = detect_currency(product["url"])

    if currency == "USD":
        return round(raw_price * rate, 2)
    return round(raw_price, 2)


def send_telegram(message):
    """Send a Telegram message to your bot."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    response = requests.post(url, json=payload, timeout=10)
    response.raise_for_status()


def load_last_prices():
    if os.path.exists(PRICES_FILE):
        with open(PRICES_FILE) as f:
            return json.load(f)
    return {}


def save_prices(prices):
    with open(PRICES_FILE, "w") as f:
        json.dump(prices, f, indent=2)


def main():
    print(f"=== Price check started: {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")

    rate = get_usd_to_aud_rate()
    print(f"Exchange rate: 1 USD = {rate} AUD\n")

    products = load_products_from_sheet()
    print(f"Tracking {len(products)} products\n")

    last_prices = load_last_prices()
    current_prices = {}
    changes = []

    for product in products:
        name = product["name"]
        try:
            price = fetch_price_in_aud(product, rate)
            if price is None:
                print(f"  SKIP  {name} (site type not supported)")
                continue

            current_prices[name] = price
            last = last_prices.get(name)

            if last is None:
                print(f"  NEW   {name}: ${price:.2f} AUD (first check, baseline saved)")
            elif price != last:
                changes.append({
                    "name": name,
                    "was": last,
                    "now": price,
                    "dropped": price < last
                })
                arrow = "📉" if price < last else "📈"
                print(f"  {arrow} {name}: was ${last:.2f} → now ${price:.2f} AUD")
            else:
                print(f"  ----  {name}: unchanged at ${price:.2f} AUD")

        except Exception as e:
            print(f"  ERROR {name}: {e}")

    save_prices(current_prices)

    if changes:
        lines = ["<b>💰 Price changes detected!</b>", ""]
        for c in changes:
            emoji = "🟢" if c["dropped"] else "🔴"
            verb = "dropped" if c["dropped"] else "increased"
            diff = abs(c["now"] - c["was"])
            lines.append(f"{emoji} <b>{c['name']}</b>")
            lines.append(f"   Price {verb} by ${diff:.2f} AUD")
            lines.append(f"   Was: ${c['was']:.2f} AUD")
            lines.append(f"   Now: ${c['now']:.2f} AUD")
            lines.append("")
        lines.append("Go check it out! 🛒")
        send_telegram("\n".join(lines))
        print(f"\n✓ Sent Telegram alert with {len(changes)} change(s)")
    else:
        send_telegram(f"✅ Prices have not changed on all products ({len(current_prices)} tracked)")
        print("\n✓ No changes detected — sent 'no changes' Telegram message")


if __name__ == "__main__":
    main()