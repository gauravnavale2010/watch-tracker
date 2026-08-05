from datetime import datetime
import json
import os
import re
import bs4
import requests

# Secrets configured in GitHub Repository Settings
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
STATE_FILE = "stock_state.json"

# Targets both HMT domains/endpoints
URLS_TO_SCRAPE = [
    "https://www.hmtwatches.store/collections/all",
    "https://www.hmtwatches.in/collections/all",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
        " like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


def load_previous_state():
    """Loads saved stock state from JSON file."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading state file: {e}")
            return {}
    return {}


def save_current_state(state):
    """Saves updated stock state to JSON file."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def send_telegram_alert(
    title, price, series, image_url, buy_url, detected_time
):
    """Sends a rich photo alert formatted exactly like the HMT Telegram channels."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram secrets missing!")
        return

    caption = (
        f"🚨 <b>HMT STORE STOCK ALERT</b>\n\n"
        f"📦 <b>Product Title:</b> {title}\n"
        f"💰 <b>Price:</b> {price}₹\n"
        f"⌚ <b>Series:</b> {series}\n"
        f"🕒 <b>Detected:</b> {detected_time}\n"
        f"✅ <b>In stock:</b> Available\n"
        f"🌐 <b>Website:</b> <a href='{buy_url}'>Hmtwatches.store</a>"
    )

    telegram_api_url = (
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "photo": image_url,
        "caption": caption,
        "parse_mode": "HTML",
    }

    try:
        res = requests.post(telegram_api_url, data=payload, timeout=10)
        print(f"Telegram response for {title}: {res.status_code}")
    except Exception as e:
        print(f"Failed to send Telegram alert: {e}")


def extract_series(title):
    """Extracts watch series from title (e.g., Galaxy, Stellar, Kohinoor, Sangam)."""
    series_keywords = [
        "Galaxy",
        "Stellar",
        "Kohinoor",
        "Sangam",
        "Tareeq",
        "Janata",
        "Pilot",
        "Vijay",
        "Sona",
        "Automatic",
        "Quartz",
    ]
    for key in series_keywords:
        if re.search(rf"\b{key}\b", title, re.IGNORECASE):
            return key
    return "General"


def scrape_watches():
    """Scrapes watches from the store endpoints."""
    current_catalog = {}

    for url in URLS_TO_SCRAPE:
        try:
            response = requests.get(url, headers=HEADERS, timeout=15)
            if response.status_code != 200:
                print(
                    f"Failed to fetch {url}, Status Code:"
                    f" {response.status_code}"
                )
                continue

            soup = bs4.BeautifulSoup(response.text, "html.parser")

            # Generic Shopify product card parser (covers hmtwatches.store & hmtwatches.in)
            products = soup.find_all(
                ["div", "li"], class_=re.compile(r"product|grid__item|card")
            )

            for prod in products:
                title_elem = prod.find(["a", "h2", "h3"], class_=re.compile(r"title|name|card__heading"))
                if not title_elem or not title_elem.text.strip():
                    continue

                title = title_elem.text.strip()

                # Get product link
                link_elem = prod.find("a", href=True)
                if not link_elem:
                    continue
                buy_url = (
                    "https://www.hmtwatches.store" + link_elem["href"]
                    if link_elem["href"].startswith("/")
                    else link_elem["href"]
                )

                # Stock check
                text_content = prod.text.lower()
                is_available = "sold out" not in text_content and "out of stock" not in text_content

                # Get price
                price_match = re.search(
                    r"Rs\.\s*([\d,]+)|₹\s*([\d,]+)|([\d,]+)\s*₹", prod.text
                )
                price = "N/A"
                if price_match:
                    price = next(
                        g for g in price_match.groups() if g is not None
                    ).replace(",", "")

                # Get image URL
                img_elem = prod.find("img")
                img_url = ""
                if img_elem:
                    src = (
                        img_elem.get("data-src")
                        or img_elem.get("src")
                        or img_elem.get("srcset", "").split(" ")[0]
                    )
                    if src:
                        img_url = "https:" + src if src.startswith("//") else src

                if not img_url:
                    # Fallback placeholder image if no image found
                    img_url = "https://cdn.shopify.com/s/files/1/0281/8172/5283/files/hmt_logo.png"

                product_id = re.sub(r"\W+", "_", title.lower())

                current_catalog[product_id] = {
                    "title": title,
                    "price": price,
                    "series": extract_series(title),
                    "image_url": img_url,
                    "buy_url": buy_url,
                    "available": is_available,
                }

        except Exception as e:
            print(f"Error scraping {url}: {e}")

    return current_catalog


def main():
    previous_state = load_previous_state()
    current_catalog = scrape_watches()

    print(f"Scraped {len(current_catalog)} products total.")

    now_str = datetime.now().strftime("%d %b %Y, %I:%M %p").lower()
    updated_state = dict(previous_state)

    for prod_id, item in current_catalog.items():
        was_available = previous_state.get(prod_id, {}).get("available", False)
        is_available = item["available"]

        # Alert trigger condition: Item transitions from Unavailable -> Available
        if is_available and not was_available:
            print(f"ALERT: Restock detected for {item['title']}")
            send_telegram_alert(
                title=item["title"],
                price=item["price"],
                series=item["series"],
                image_url=item["image_url"],
                buy_url=item["buy_url"],
                detected_time=now_str,
            )

        updated_state[prod_id] = {"available": is_available, "title": item["title"]}

    save_current_state(updated_state)


if __name__ == "__main__":
    main()
