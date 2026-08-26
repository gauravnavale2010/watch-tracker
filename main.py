from datetime import datetime, timezone
import json
import os
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


# ============================================================
# CONFIGURATION
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

STATE_FILE = "stock_state.json"

STORE_URL = "https://www.hmtwatches.store/collections/all"
HMT_IN_URL = "https://www.hmtwatches.in/all_product"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_TIMEOUT = 20

# Products/series you specifically don't want.
EXCLUDED_KEYWORDS = [
    "galaxy",
    "inox",
    "inox gold",
]


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()
session.headers.update(HEADERS)


# ============================================================
# STATE
# ============================================================

def load_previous_state():
    if not os.path.exists(STATE_FILE):
        print("No previous state file found. Creating initial baseline.")
        return {}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading state file: {e}")
        return {}


def save_current_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

    print(f"Saved {len(state)} products to {STATE_FILE}")


# ============================================================
# FILTERING
# ============================================================

def is_excluded(title):
    """
    Ignore Galaxy and Inox products.
    Case-insensitive.
    """
    title_lower = title.lower()

    for keyword in EXCLUDED_KEYWORDS:
        if keyword in title_lower:
            return True

    return False


# ============================================================
# HELPERS
# ============================================================

def clean_text(text):
    if not text:
        return ""

    return re.sub(r"\s+", " ", text).strip()


def extract_price(text):
    """
    Attempts to extract an INR price.
    """

    patterns = [
        r"₹\s*([\d,]+)",
        r"Rs\.?\s*([\d,]+)",
        r"INR\s*([\d,]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)

        if match:
            return match.group(1).replace(",", "")

    return "N/A"


def extract_quantity(text):
    """
    Try to find an explicit inventory quantity if the site exposes one.

    Examples detected:
        5 in stock
        5 left
        only 3 available
        quantity: 7

    If no explicit quantity is exposed, return None.
    """

    patterns = [
        r"(\d+)\s+in\s+stock",
        r"(\d+)\s+left",
        r"only\s+(\d+)\s+available",
        r"only\s+(\d+)\s+left",
        r"quantity\s*[:\-]?\s*(\d+)",
        r"stock\s*[:\-]?\s*(\d+)",
    ]

    text_lower = text.lower()

    for pattern in patterns:
        match = re.search(pattern, text_lower)

        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass

    return None


def determine_availability(text):
    """
    Determine whether a product appears to be available.

    Explicit out-of-stock text takes priority.
    """

    text_lower = text.lower()

    out_of_stock_terms = [
        "out of stock",
        "out-of-stock",
        "sold out",
        "unavailable",
    ]

    for term in out_of_stock_terms:
        if term in text_lower:
            return False

    in_stock_terms = [
        "in stock",
        "available",
        "add to cart",
        "buy now",
    ]

    for term in in_stock_terms:
        if term in text_lower:
            return True

    # If the page doesn't explicitly say out of stock,
    # treat it as potentially available.
    return True


def extract_series(title):
    """
    Best-effort series extraction.
    """

    series_keywords = [
        "Kohinoor",
        "Janata Automatic",
        "Janata",
        "Pilot Automatic",
        "Pilot",
        "Jawan Automatic",
        "Jawan",
        "Vijay",
        "Pace",
        "Sona",
        "Swarna",
        "Kedar",
        "Kanchan",
        "Kajal",
        "Kapila",
        "Karna",
        "Jhalak",
        "Himalaya",
        "Kailash",
        "Commando",
        "Vivek",
        "Bahadur",
        "Gandaberunda",
        "Ravi",
        "Skeleton",
        "Chronograph",
        "Track",
        "HQ Series",
        "Sourab",
        "Sougandh",
        "NASS",
        "Operation Sindoor",
    ]

    for keyword in series_keywords:
        if re.search(
            rf"\b{re.escape(keyword)}\b",
            title,
            re.IGNORECASE,
        ):
            return keyword

    return "General"


def make_product_id(source, title, url):
    """
    Create a stable ID.

    URL is preferred because two products can have similar names.
    """

    identity = f"{source}|{url}|{title}".lower()

    return re.sub(r"\W+", "_", identity).strip("_")


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_alert(item, alert_type, previous_item=None):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("ERROR: Telegram secrets are missing.")
        return False

    title = item["title"]
    price = item["price"]
    series = item["series"]
    image_url = item["image_url"]
    buy_url = item["buy_url"]
    source = item["source"]
    quantity = item.get("quantity")

    if alert_type == "NEW":
        header = "🆕 <b>NEW HMT WATCH</b>"
    elif alert_type == "RESTOCK":
        header = "🚨 <b>HMT RESTOCK ALERT</b>"
    else:
        header = "📦 <b>HMT STOCK UPDATE</b>"

    if quantity is not None:
        stock_text = f"{quantity} units"
    elif item["available"]:
        stock_text = "In Stock"
    else:
        stock_text = "Out Of Stock"

    caption = (
        f"{header}\n\n"
        f"📦 <b>Product:</b> {title}\n"
        f"💰 <b>Price:</b> ₹{price}\n"
        f"⌚ <b>Series:</b> {series}\n"
        f"🌐 <b>Source:</b> {source}\n"
        f"📊 <b>Stock:</b> {stock_text}\n"
    )

    if alert_type == "RESTOCK" and previous_item:
        old_quantity = previous_item.get("quantity")

        if old_quantity is not None and quantity is not None:
            caption += (
                f"📈 <b>Change:</b> "
                f"{old_quantity} → {quantity} units\n"
            )

    detected_time = datetime.now(
        timezone.utc
    ).strftime("%d %b %Y, %I:%M %p UTC")

    caption += (
        f"🕒 <b>Detected:</b> {detected_time}\n"
        f"🔗 <a href='{buy_url}'>View Product</a>"
    )

    api_url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendPhoto"
    )

    # If image is unavailable, send a normal message instead.
    if not image_url:
        api_url = (
            f"https://api.telegram.org/bot"
            f"{TELEGRAM_BOT_TOKEN}/sendMessage"
        )

        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": caption,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }

        try:
            response = session.post(
                api_url,
                data=payload,
                timeout=REQUEST_TIMEOUT,
            )

            print(
                f"Telegram {alert_type} response: "
                f"{response.status_code}"
            )
            print(response.text)

            return response.ok

        except Exception as e:
            print(f"Telegram error: {e}")
            return False

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "photo": image_url,
        "caption": caption,
        "parse_mode": "HTML",
    }

    try:
        response = session.post(
            api_url,
            data=payload,
            timeout=REQUEST_TIMEOUT,
        )

        print(
            f"Telegram {alert_type} response: "
            f"{response.status_code}"
        )
        print(response.text)

        return response.ok

    except Exception as e:
        print(f"Telegram error: {e}")
        return False


# ============================================================
# HMT STORE SCRAPER
# ============================================================

def scrape_store():
    """
    Scrape the Shopify-style HMT Store.

    This is intentionally more flexible than the original parser.
    """

    products = {}

    print("\n==========================================")
    print("SCRAPING HMT STORE")
    print("==========================================")

    try:
        response = session.get(
            STORE_URL,
            timeout=REQUEST_TIMEOUT,
        )

        print(
            f"HMT Store HTTP status: "
            f"{response.status_code}"
        )

        if response.status_code != 200:
            print(
                f"HMT Store failed: "
                f"{response.status_code}"
            )
            return products

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        # Shopify product links are usually product/... URLs.
        product_links = soup.select(
            'a[href*="/products/"]'
        )

        print(
            f"HMT Store product links found: "
            f"{len(product_links)}"
        )

        seen_urls = set()

        for link in product_links:
            href = link.get("href")

            if not href:
                continue

            product_url = urljoin(
                STORE_URL,
                href,
            )

            if product_url in seen_urls:
                continue

            seen_urls.add(product_url)

            # Find surrounding product card.
            card = (
                link.find_parent(
                    class_=re.compile(
                        r"card|product|grid",
                        re.IGNORECASE,
                    )
                )
                or link.parent
            )

            card_text = clean_text(
                card.get_text(" ", strip=True)
            )

            # Product title.
            title = clean_text(
                link.get_text(" ", strip=True)
            )

            if not title:
                title_elem = card.find(
                    ["h2", "h3", "h4"]
                )

                if title_elem:
                    title = clean_text(
                        title_elem.get_text(
                            " ",
                            strip=True,
                        )
                    )

            if not title:
                continue

            if is_excluded(title):
                continue

            # Image.
            image_url = ""

            img = card.find("img")

            if img:
                image_url = (
                    img.get("src")
                    or img.get("data-src")
                    or ""
                )

                if image_url.startswith("//"):
                    image_url = "https:" + image_url

                image_url = urljoin(
                    STORE_URL,
                    image_url,
                )

            # Availability.
            available = determine_availability(
                card_text
            )

            # Quantity if exposed.
            quantity = extract_quantity(
                card_text
            )

            price = extract_price(card_text)

            product_id = make_product_id(
                "STORE",
                title,
                product_url,
            )

            products[product_id] = {
                "title": title,
                "price": price,
                "series": extract_series(title),
                "image_url": image_url,
                "buy_url": product_url,
                "available": available,
                "quantity": quantity,
                "source": "HMT Store",
            }

    except Exception as e:
        print(f"HMT Store scraping error: {e}")

    print(
        f"HMT Store usable products: "
        f"{len(products)}"
    )

    return products


# ============================================================
# HMT.IN SCRAPER
# ============================================================

def scrape_hmt_in():
    """
    Scrape the official HMT .in catalogue.

    The site currently exposes products through /all_product
    and displays Out Of Stock status on catalogue cards.
    """

    products = {}

    print("\n==========================================")
    print("SCRAPING HMT.IN")
    print("==========================================")

    try:
        response = session.get(
            HMT_IN_URL,
            timeout=REQUEST_TIMEOUT,
        )

        print(
            f"HMT.in HTTP status: "
            f"{response.status_code}"
        )

        if response.status_code != 200:
            print(
                f"HMT.in failed: "
                f"{response.status_code}"
            )
            return products

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        # Try product links first.
        links = soup.find_all(
            "a",
            href=True,
        )

        print(
            f"HMT.in links found: "
            f"{len(links)}"
        )

        seen_urls = set()

        for link in links:

            href = link.get("href", "").strip()

            if not href:
                continue

            # Ignore obvious navigation links.
            if (
                href.startswith("#")
                or href.startswith("javascript:")
                or href.startswith("mailto:")
            ):
                continue

            product_url = urljoin(
                HMT_IN_URL,
                href,
            )

            # Product cards on this site may use
            # product_overview URLs.
            if (
                "product_overview" not in product_url
                and "/product/" not in product_url
            ):
                continue

            if product_url in seen_urls:
                continue

            seen_urls.add(product_url)

            # Product card.
            card = (
                link.find_parent(
                    class_=re.compile(
                        r"product|item|card|col",
                        re.IGNORECASE,
                    )
                )
                or link.parent
            )

            card_text = clean_text(
                card.get_text(
                    " ",
                    strip=True,
                )
            )

            # Look for title.
            title = clean_text(
                link.get_text(
                    " ",
                    strip=True,
                )
            )

            if not title:
                title_elem = card.find(
                    ["h2", "h3", "h4", "h5"]
                )

                if title_elem:
                    title = clean_text(
                        title_elem.get_text(
                            " ",
                            strip=True,
                        )
                    )

            # Some cards contain nested spans.
            if not title or len(title) < 3:
                possible_texts = []

                for element in card.find_all(
                    ["h2", "h3", "h4", "h5", "span"]
                ):
                    text = clean_text(
                        element.get_text(
                            " ",
                            strip=True,
                        )
                    )

                    if text:
                        possible_texts.append(text)

                # Pick the first plausible HMT product title.
                for candidate in possible_texts:
                    if (
                        "HMT" in candidate
                        and "RS." not in candidate.upper()
                    ):
                        title = candidate
                        break

            if not title:
                continue

            # Remove "Load More" etc.
            if title.lower() in {
                "load more",
                "login",
                "register",
                "wishlist",
                "cart",
            }:
                continue

            if is_excluded(title):
                continue

            # Image.
            image_url = ""

            img = card.find("img")

            if img:
                image_url = (
                    img.get("src")
                    or img.get("data-src")
                    or img.get("data-original")
                    or ""
                )

                if image_url.startswith("//"):
                    image_url = "https:" + image_url

                image_url = urljoin(
                    HMT_IN_URL,
                    image_url,
                )

            available = determine_availability(
                card_text
            )

            quantity = extract_quantity(
                card_text
            )

            price = extract_price(card_text)

            product_id = make_product_id(
                "HMT_IN",
                title,
                product_url,
            )

            products[product_id] = {
                "title": title,
                "price": price,
                "series": extract_series(title),
                "image_url": image_url,
                "buy_url": product_url,
                "available": available,
                "quantity": quantity,
                "source": "HMT.in",
            }

    except Exception as e:
        print(f"HMT.in scraping error: {e}")

    print(
        f"HMT.in usable products: "
        f"{len(products)}"
    )

    return products


# ============================================================
# COMBINE
# ============================================================

def scrape_all_sites():
    store_products = scrape_store()
    hmt_in_products = scrape_hmt_in()

    combined = {}

    combined.update(store_products)
    combined.update(hmt_in_products)

    print("\n==========================================")
    print("SCRAPE SUMMARY")
    print("==========================================")
    print(f"HMT Store: {len(store_products)}")
    print(f"HMT.in:    {len(hmt_in_products)}")
    print(f"TOTAL:     {len(combined)}")

    return combined


# ============================================================
# MAIN STOCK LOGIC
# ============================================================

def main():
    print("\n")
    print("====================================================")
    print("HMT WATCH RESTOCK TRACKER")
    print("====================================================")

    previous_state = load_previous_state()

    current_catalog = scrape_all_sites()

    if not current_catalog:
        print(
            "\nWARNING: ZERO PRODUCTS SCRAPED."
        )
        print(
            "State will NOT be overwritten."
        )
        print(
            "This prevents a temporary website "
            "failure from generating false alerts."
        )
        return

    print(
        f"\nProcessing {len(current_catalog)} products..."
    )

    updated_state = dict(previous_state)

    new_products = []
    restocks = []
    quantity_changes = []

    for product_id, item in current_catalog.items():

        previous = previous_state.get(
            product_id
        )

        # ----------------------------------------------------
        # FIRST TIME SEEN
        # ----------------------------------------------------

        if previous is None:

            # Establish baseline.
            #
            # We DO NOT alert here because otherwise the
            # first run would send potentially hundreds
            # of notifications.
            print(
                f"NEW BASELINE: "
                f"{item['title']} "
                f"[{item['source']}]"
            )

            updated_state[product_id] = item
            continue

        was_available = previous.get(
            "available",
            False,
        )

        is_available = item.get(
            "available",
            False,
        )

        old_quantity = previous.get(
            "quantity"
        )

        new_quantity = item.get(
            "quantity"
        )

        # ----------------------------------------------------
        # RESTOCK
        # ----------------------------------------------------

        if is_available and not was_available:

            print(
                f"🚨 RESTOCK: "
                f"{item['title']} "
                f"[{item['source']}]"
            )

            success = send_telegram_alert(
                item,
                "RESTOCK",
                previous,
            )

            if success:
                restocks.append(item)

        # ----------------------------------------------------
        # QUANTITY CHANGE
        # ----------------------------------------------------

        elif (
            is_available
            and old_quantity is not None
            and new_quantity is not None
            and old_quantity != new_quantity
        ):

            print(
                f"📦 QUANTITY CHANGE: "
                f"{item['title']} "
                f"{old_quantity} → "
                f"{new_quantity}"
            )

            quantity_changes.append(item)

        # ----------------------------------------------------
        # UPDATE STATE
        # ----------------------------------------------------

        updated_state[product_id] = item

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    save_current_state(updated_state)

    print("\n====================================================")
    print("RUN COMPLETE")
    print("====================================================")
    print(
        f"Products monitored: "
        f"{len(current_catalog)}"
    )
    print(
        f"Restocks detected: "
        f"{len(restocks)}"
    )
    print(
        f"Quantity changes: "
        f"{len(quantity_changes)}"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
