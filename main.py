import os
import json
import requests
from bs4 import BeautifulSoup

# Load credentials from GitHub Secrets
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
CACHE_FILE = "stock_state.json"

TRACK_URLS = [
    "https://www.hmtwatches.in/shop_type?type=shop_type&id=8",  # Mechanical
    "https://www.hmtwatches.in/shop_type?type=shop_type&id=9",  # Automatic
    "https://www.hmtwatches.in/shop_type?type=shop_type&id=10" # Quartz
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"
}

def send_telegram_alert(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error sending alert: {e}")

def load_previous_stock() -> dict:
    try:
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_current_stock(stock_data: dict):
    with open(CACHE_FILE, "w") as f:
        json.dump(stock_data, f, indent=2)

def scrape_hmt():
    current_catalog = {}
    for url in TRACK_URLS:
        try:
            res = requests.get(url, headers=HEADERS, timeout=15)
            if res.status_code != 200:
                continue
            soup = BeautifulSoup(res.text, "html.parser")
            cards = soup.find_all("div", class_="product-grid") or soup.find_all("div", class_="product-box")
            
            for card in cards:
                title_elem = card.find("a") or card.find("h3") or card.find("h4")
                if not title_elem:
                    continue
                title = title_elem.get_text(strip=True)
                link = title_elem.get("href", url)
                if link and not link.startswith("http"):
                    link = f"https://www.hmtwatches.in/{link.lstrip('/')}"
                
                card_text = card.get_text().lower()
                is_in_stock = "out of stock" not in card_text and "sold out" not in card_text
                current_catalog[title] = {"in_stock": is_in_stock, "link": link}
        except Exception as e:
            print(f"Error scraping {url}: {e}")
    return current_catalog

def run_check():
    previous = load_previous_stock()
    current = scrape_hmt()
    if not current:
        print("No watches scraped.")
        return

    for watch, info in current.items():
        prev_info = previous.get(watch)
        is_now_in_stock = info["in_stock"]
        was_in_stock = prev_info["in_stock"] if prev_info else False

        if is_now_in_stock and not was_in_stock:
            msg = (
                f"🚨 HMT Watch Restock Alert!\n\n"
                f"⌚ {watch} is back IN STOCK!\n\n"
                f"🔗 Buy on HMT Website"
            )
            send_telegram_alert(msg)
            print(f"Alert triggered for {watch}")

    save_current_stock(current)

if __name__ == "__main__":
    run_check()
