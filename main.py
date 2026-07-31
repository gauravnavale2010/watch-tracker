import os
import json
import requests
from bs4 import BeautifulSoup

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
CACHE_FILE = "stock_state.json"

# Key HMT URLs
HMT_IN_URLS = [
    "https://hmtwatches.in/watches",
    "https://hmtwatches.in/shop_type?type=shop_type&id=8",   # Mechanical
    "https://hmtwatches.in/shop_type?type=shop_type&id=9",   # Automatic
    "https://hmtwatches.in/shop_type?type=shop_type&id=10"   # Quartz
]

HMT_STORE_URL = "https://www.hmtwatches.store/all-products"

def get_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache"
    })
    return session

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

def scrape_hmt_in(session):
    catalog = {}
    for url in HMT_IN_URLS:
        try:
            res = session.get(url, timeout=15)
            if res.status_code != 200:
                print(f"Failed to fetch {url}: Status {res.status_code}")
                continue
            soup = BeautifulSoup(res.text, "html.parser")
            cards = soup.find_all("div", class_="product-grid") or soup.find_all("div", class_="product-box") or soup.find_all("div", class_="pro-box")
            
            for card in cards:
                title_elem = card.find("a") or card.find("h3") or card.find("h4")
                if not title_elem:
                    continue
                title = title_elem.get_text(strip=True)
                if not title or len(title) < 3:
                    continue

                link = title_elem.get("href", url)
                if link and not link.startswith("http"):
                    link = f"https://hmtwatches.in/{link.lstrip('/')}"
                
                card_text = card.get_text().lower()
                is_in_stock = "out of stock" not in card_text and "sold out" not in card_text and "coming soon" not in card_text
                
                catalog[f"[hmtwatches.in] {title}"] = {"in_stock": is_in_stock, "link": link}
        except Exception as e:
            print(f"Error scraping {url}: {e}")
    return catalog

def scrape_hmt_store(session):
    catalog = {}
    try:
        res = session.get(HMT_STORE_URL, timeout=15)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            cards = soup.find_all("div", class_="product-card") or soup.find_all("a", href=True)
            
            for card in cards:
                title_elem = card.find("h2") or card.find("h3") or card.find("p") or card
                title = title_elem.get_text(strip=True) if title_elem else ""
                
                if not title or len(title) < 4 or "₹" in title or "javascript" in title.lower():
                    continue

                link = card.get("href") if card.name == "a" else (card.find("a", href=True) or {}).get("href", HMT_STORE_URL)
                if link and not link.startswith("http"):
                    link = f"https://www.hmtwatches.store{link}"
                
                card_text = card.get_text().lower()
                is_in_stock = "out of stock" not in card_text and "sold out" not in card_text
                catalog[f"[hmtwatches.store] {title}"] = {"in_stock": is_in_stock, "link": link}
    except Exception as e:
        print(f"Error scraping hmtwatches.store: {e}")
    return catalog

def run_check():
    session = get_session()
    previous = load_previous_stock()
    
    current = {}
    current.update(scrape_hmt_in(session))
    current.update(scrape_hmt_store(session))

    print(f"Total watches scraped: {len(current)}")

    if not current:
        print("No watches scraped. Site might be blocking requests.")
        return

    for watch, info in current.items():
        prev_info = previous.get(watch)
        is_now_in_stock = info["in_stock"]
        was_in_stock = prev_info["in_stock"] if prev_info else False

        is_new_watch = prev_info is None and is_now_in_stock
        is_restocked = was_in_stock is False and is_now_in_stock

        if is_new_watch or is_restocked:
            alert_type = "🆕 New Watch Listing!" if is_new_watch else "🚨 Watch Restock Alert!"
            msg = (
                f"<b>{alert_type}</b>\n\n"
                f"⌚ <b>{watch}</b> is available!\n\n"
                f"🔗 <a href='{info['link']}'>Buy Now</a>"
            )
            send_telegram_alert(msg)
            print(f"Alert triggered for {watch}")

    save_current_stock(current)

if __name__ == "__main__":
    run_check()
