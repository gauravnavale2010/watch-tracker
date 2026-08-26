from datetime import datetime
import html
import json
import os
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


# ============================================================
# CONFIG
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

STATE_FILE = "stock_state.json"

HMT_STORE_URL = "https://www.hmtwatches.store/collections/all"
HMT_IN_URL = "https://hmtwatches.in/all_product"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# User requested these to be excluded.
EXCLUDED_PATTERNS = [
    r"\bgalaxy\b",
    r"\binox\b",
    r"\binox gold\b",
]


# ============================================================
# HELPERS
# ============================================================

def clean_text(text):
    """Normalize whitespace."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def is_excluded(title):
    """Return True for Galaxy / INOX / Inox Gold watches."""
    title_lower = title.lower()

    for pattern in EXCLUDED_PATTERNS:
        if re.search(pattern, title_lower, re.IGNORECASE):
            return True

    return False


def normalize_price(text):
    """Extract a rupee price."""
    if not text:
        return "N/A"

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
    Try to detect quantity when the website exposes it.

    Examples detected:
      Only 3 left
      3 left in stock
      3 units available
      Quantity: 3
      Stock: 3
    """

    if not text:
        return None

    patterns = [
        r"only\s+(\d+)\s+left",
        r"only\s+(\d+)\s+left\s+in\s+stock",
        r"(\d+)\s+left\s+in\s+stock",
        r"(\d+)\s+units?\s+available",
        r"quantity\s*[:\-]\s*(\d+)",
        r"stock\s*[:\-]\s*(\d+)",
        r"in\s+stock\s*[:\-]\s*(\d+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass

    return None


def determine_availability(text):
    """
    Determine stock status conservatively.

    Returns:
        True  = available
        False = unavailable
    """

    if not text:
        return False

    text_lower = text.lower()

    # Strong out-of-stock signals.
    unavailable_patterns = [
        "out of stock",
        "sold out",
        "currently unavailable",
        "not available",
        "unavailable",
    ]

    for phrase in unavailable_patterns:
        if phrase in text_lower:
            return False

    # Coming soon should NOT be treated as available.
    if "coming soon" in text_lower:
        return False

    # Explicit positive stock signals.
    available_patterns = [
        "in stock",
        "add to cart",
        "buy now",
        "available",
    ]

    for phrase in available_patterns:
        if phrase in text_lower:
            return True

    # If there is no explicit negative signal but the page is
    # clearly a product listing, assume available.
    return True


def extract_image(card):
    """Extract image URL from a product card."""
    img = card.find("img")

    if not img:
        return ""

    candidates = [
        img.get("src"),
        img.get("data-src"),
        img.get("data-original"),
    ]

    # srcset may contain multiple images.
    srcset = img.get("srcset")
    if srcset:
        first = srcset.split(",")[0].strip().split(" ")[0]
        candidates.append(first)

    for value in candidates:
        if value:
            return urljoin("https://hmtwatches.in", value)

    return ""


def extract_series(title):
    """Extract a useful series name."""
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
        "Pace",
        "Plus",
        "Vihaan",
        "Elegance",
        "Utsav",
        "Roman",
        "Economic",
        "Swarna",
        "Chronograph",
        "Jhalak",
        "Kedar",
        "Kanchan",
        "Rajat",
        "Skeleton",
        "Himalaya",
        "Kailash",
        "Commando",
        "Gandaberunda",
        "Ravi",
        "Bahadur",
    ]

    for key in series_keywords:
        if re.search(rf"\b{re.escape(key)}\b", title, re.IGNORECASE):
            return key

    return "General"


# ============================================================
# STATE
# ============================================================

def load_previous_state():
    """Load stock state."""
    if not os.path.exists(STATE_FILE):
        return {}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)

        if not isinstance(state, dict):
            return {}

        return state

    except Exception as e:
        print(f"Error loading state file: {e}")
        return {}


def save_current_state(state):
    """Save stock state."""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def state_is_old_format(state):
    """
    Detect your previous title-based state.

    The new version uses URL-based IDs, so the old 352-entry
    state should not be allowed to generate false alerts.
    """

    if not state:
        return False

    for value in state.values():
        if isinstance(value, dict):
            if "source" in value and "url" in value:
                return False

    return True


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_alert(item, alert_type):
    """Send stock alert to Telegram."""

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("ERROR: Telegram secrets are missing.")
        return False

    title = html.escape(item["title"])
    price = html.escape(str(item["price"]))
    series = html.escape(item["series"])
    source = html.escape(item["source"])
    buy_url = html.escape(item["buy_url"], quote=True)

    quantity = item.get("quantity")

    if quantity is not None:
        quantity_text = f"📦 <b>Units:</b> {quantity}\n"
    else:
        quantity_text = "📦 <b>Units:</b> Not exposed by website\n"

    if alert_type == "RESTOCK":
        heading = "🔔 <b>HMT RESTOCK ALERT</b>"
    else:
        heading = "🆕 <b>NEW HMT WATCH ALERT</b>"

    detected_time = datetime.now().strftime(
        "%d %b %Y, %I:%M:%S %p"
    )

    caption = (
        f"{heading}\n\n"
        f"⌚ <b>Product:</b> {title}\n"
        f"💰 <b>Price:</b> ₹{price}\n"
        f"🏷️ <b>Series:</b> {series}\n"
        f"🌐 <b>Source:</b> {source}\n"
        f"{quantity_text}"
        f"🕒 <b>Detected:</b> {detected_time}\n\n"
        f"🛒 <a href='{buy_url}'>Open product</a>"
    )

    # Try photo first.
    image_url = item.get("image_url")

    if image_url:
        telegram_url = (
            f"https://api.telegram.org/"
            f"bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        )

        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "photo": image_url,
            "caption": caption,
            "parse_mode": "HTML",
        }

        try:
            response = requests.post(
                telegram_url,
                data=payload,
                timeout=15,
            )

            if response.ok:
                print(
                    f"Telegram photo alert sent: "
                    f"{item['title']}"
                )
                return True

            print(
                f"Telegram sendPhoto failed "
                f"({response.status_code}): "
                f"{response.text[:300]}"
            )

        except Exception as e:
            print(f"Telegram sendPhoto error: {e}")

    # Fallback to text message.
    telegram_url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": caption,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    try:
        response = requests.post(
            telegram_url,
            data=payload,
            timeout=15,
        )

        if response.ok:
            print(
                f"Telegram text alert sent: "
                f"{item['title']}"
            )
            return True

        print(
            f"Telegram sendMessage failed "
            f"({response.status_code}): "
            f"{response.text[:300]}"
        )

    except Exception as e:
        print(f"Telegram sendMessage error: {e}")

    return False


# ============================================================
# HMT STORE
# ============================================================

def scrape_hmt_store():
    """
    Scrape hmtwatches.store.

    IMPORTANT:
    The Store uses /product/<UUID>-style URLs rather than
    Shopify /products/... URLs.
    """

    print("=" * 42)
    print("SCRAPING HMT STORE")
    print("=" * 42)

    catalog = {}

    try:
        response = requests.get(
            HMT_STORE_URL,
            headers=HEADERS,
            timeout=20,
        )

        print(
            f"HMT Store HTTP status: "
            f"{response.status_code}"
        )

        if response.status_code != 200:
            print("HMT Store request failed.")
            return catalog

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        # The Store product URLs use /product/.
        product_links = []

        for a in soup.find_all("a", href=True):
            href = a["href"]

            if re.search(
                r"/product/",
                href,
                re.IGNORECASE,
            ):
                product_links.append(a)

        # Deduplicate links.
        unique_urls = {}

        for a in product_links:
            url = urljoin(HMT_STORE_URL, a["href"])

            if url not in unique_urls:
                unique_urls[url] = a

        print(
            f"HMT Store product links found: "
            f"{len(unique_urls)}"
        )

        for product_url, link in unique_urls.items():

            # Find the closest reasonable product container.
            card = (
                link.find_parent(
                    ["article", "li", "div"]
                )
                or link
            )

            card_text = clean_text(card.get_text(" ", strip=True))

            # Product title.
            title = clean_text(link.get_text(" ", strip=True))

            if not title:
                # Try headings in the card.
                heading = card.find(
                    ["h1", "h2", "h3", "h4"]
                )

                if heading:
                    title = clean_text(
                        heading.get_text(" ", strip=True)
                    )

            if not title:
                continue

            # Exclusions.
            if is_excluded(title):
                continue

            price = normalize_price(card_text)
            available = determine_availability(card_text)
            quantity = extract_quantity(card_text)
            image_url = extract_image(card)

            product_id = (
                f"store:"
                f"{product_url.rstrip('/').lower()}"
            )

            catalog[product_id] = {
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
        f"{len(catalog)}"
    )

    return catalog


# ============================================================
# HMT.IN
# ============================================================

def scrape_hmt_in():
    """
    Scrape official hmtwatches.in.

    The catalogue contains duplicate cards, so the actual
    product URL is used as the unique product ID.
    """

    print("=" * 42)
    print("SCRAPING HMT.IN")
    print("=" * 42)

    catalog = {}

    try:
        response = requests.get(
            HMT_IN_URL,
            headers=HEADERS,
            timeout=20,
        )

        print(
            f"HMT.in HTTP status: "
            f"{response.status_code}"
        )

        if response.status_code != 200:
            print("HMT.in request failed.")
            return catalog

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        # Find product links.
        product_links = []

        for a in soup.find_all("a", href=True):

            href = a["href"]

            # HMT.in uses /product/<id>
            if re.search(
                r"/product/",
                href,
                re.IGNORECASE,
            ):
                product_links.append(a)

        print(
            f"HMT.in product links found: "
            f"{len(product_links)}"
        )

        # Deduplicate by URL.
        unique_products = {}

        for a in product_links:

            product_url = urljoin(
                HMT_IN_URL,
                a["href"],
            )

            product_url = product_url.split("?")[0]

            if product_url not in unique_products:
                unique_products[product_url] = a

        print(
            f"HMT.in unique product URLs: "
            f"{len(unique_products)}"
        )

        for product_url, link in unique_products.items():

            # Get the nearest useful container.
            card = (
                link.find_parent(
                    ["article", "li", "div"]
                )
                or link
            )

            card_text = clean_text(
                card.get_text(
                    " ",
                    strip=True,
                )
            )

            # Title.
            title = clean_text(
                link.get_text(
                    " ",
                    strip=True,
                )
            )

            if not title:
                heading = card.find(
                    ["h1", "h2", "h3", "h4"]
                )

                if heading:
                    title = clean_text(
                        heading.get_text(
                            " ",
                            strip=True,
                        )
                    )

            if not title:
                continue

            # Exclusions.
            if is_excluded(title):
                continue

            price = normalize_price(card_text)
            available = determine_availability(card_text)
            quantity = extract_quantity(card_text)
            image_url = extract_image(card)

            product_id = (
                f"in:"
                f"{product_url.rstrip('/').lower()}"
            )

            catalog[product_id] = {
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
        f"{len(catalog)}"
    )

    return catalog


# ============================================================
# MAIN SCRAPER
# ============================================================

def scrape_all():
    """Scrape both HMT websites."""

    store_catalog = scrape_hmt_store()
    in_catalog = scrape_hmt_in()

    combined = {}

    combined.update(store_catalog)
    combined.update(in_catalog)

    print("=" * 42)
    print("SCRAPE SUMMARY")
    print("=" * 42)

    print(f"HMT Store: {len(store_catalog)}")
    print(f"HMT.in:    {len(in_catalog)}")
    print(f"TOTAL:     {len(combined)}")

    return combined


# ============================================================
# STOCK COMPARISON
# ============================================================

def process_stock(current_catalog, previous_state):

    print()
    print("=" * 42)
    print("PROCESSING STOCK")
    print("=" * 42)

    updated_state = {}

    restocks = 0
    new_products = 0
    quantity_changes = 0

    for product_id, item in current_catalog.items():

        previous = previous_state.get(product_id)

        current_available = item["available"]
        current_quantity = item.get("quantity")

        # ----------------------------------------------------
        # NEW PRODUCT
        # ----------------------------------------------------

        if previous is None:

            print(
                f"NEW BASELINE: "
                f"{item['title'][:35]} "
                f"[{item['source']}]"
            )

            # IMPORTANT:
            # We establish the baseline without sending
            # alerts for everything currently in stock.
            updated_state[product_id] = {
                "title": item["title"],
                "price": item["price"],
                "available": current_available,
                "quantity": current_quantity,
                "source": item["source"],
                "url": item["buy_url"],
            }

            continue

        previous_available = previous.get(
            "available",
            False,
        )

        previous_quantity = previous.get(
            "quantity"
        )

        # ----------------------------------------------------
        # RESTOCK
        # ----------------------------------------------------

        if current_available and not previous_available:

            print(
                f"🚨 RESTOCK: "
                f"{item['title']} "
                f"[{item['source']}]"
            )

            if send_telegram_alert(
                item,
                "RESTOCK",
            ):
                restocks += 1

        # ----------------------------------------------------
        # QUANTITY CHANGE
        # ----------------------------------------------------

        if (
            current_quantity is not None
            and previous_quantity is not None
            and current_quantity != previous_quantity
        ):
            print(
                f"QUANTITY CHANGE: "
                f"{item['title']} "
                f"{previous_quantity} -> "
                f"{current_quantity}"
            )

            quantity_changes += 1

        # ----------------------------------------------------
        # SAVE STATE
        # ----------------------------------------------------

        updated_state[product_id] = {
            "title": item["title"],
            "price": item["price"],
            "available": current_available,
            "quantity": current_quantity,
            "source": item["source"],
            "url": item["buy_url"],
        }

    return (
        updated_state,
        new_products,
        restocks,
        quantity_changes,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)
    print("HMT WATCH RESTOCK TRACKER")
    print("=" * 60)

    previous_state = load_previous_state()

    # --------------------------------------------------------
    # IMPORTANT STATE MIGRATION
    # --------------------------------------------------------

    if state_is_old_format(previous_state):

        print()
        print(
            "OLD STATE FORMAT DETECTED."
        )

        print(
            "Ignoring previous state and creating "
            "a clean baseline."
        )

        previous_state = {}

    current_catalog = scrape_all()

    print()
    print(
        f"Processing "
        f"{len(current_catalog)} products..."
    )

    if not current_catalog:

        print()
        print(
            "WARNING: ZERO PRODUCTS FOUND."
        )

        print(
            "Stock state will NOT be overwritten."
        )

        return

    (
        updated_state,
        new_products,
        restocks,
        quantity_changes,
    ) = process_stock(
        current_catalog,
        previous_state,
    )

    save_current_state(updated_state)

    print()
    print(
        f"Saved "
        f"{len(updated_state)} products "
        f"to {STATE_FILE}"
    )

    print()
    print("=" * 60)
    print("RUN COMPLETE")
    print("=" * 60)

    print(
        f"Products monitored: "
        f"{len(updated_state)}"
    )

    print(
        f"Restocks detected: "
        f"{restocks}"
    )

    print(
        f"Quantity changes: "
        f"{quantity_changes}"
    )


if __name__ == "__main__":
    main()
