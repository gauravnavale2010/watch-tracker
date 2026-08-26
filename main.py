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

# Correct current catalogue URLs
HMT_STORE_URL = "https://www.hmtwatches.store/all-products"
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


# ============================================================
# EXCLUSIONS
# ============================================================

def is_excluded(title):
    """
    Exclude Galaxy and all INOX watches.
    """
    t = title.lower()

    if re.search(r"\bgalaxy\b", t):
        return True

    if re.search(r"\binox\b", t):
        return True

    if "inox gold" in t:
        return True

    return False


# ============================================================
# GENERAL HELPERS
# ============================================================

def clean_text(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def absolute_url(base, url):
    if not url:
        return ""

    url = url.strip()

    if url.startswith("//"):
        return "https:" + url

    return urljoin(base, url)


def normalize_price(text):
    if not text:
        return "N/A"

    patterns = [
        r"₹\s*([\d,]+)",
        r"Rs\.?\s*([\d,]+)",
        r"RS\.?\s*([\d,]+)",
        r"INR\s*([\d,]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)

        if match:
            return match.group(1).replace(",", "")

    return "N/A"


def extract_quantity(text):
    """
    Detect exact quantity if the website exposes it.

    Examples:
        Only 3 left
        3 left in stock
        3 units available
        Stock: 3
    """

    if not text:
        return None

    patterns = [
        r"only\s+(\d+)\s+left",
        r"only\s+(\d+)\s+left\s+in\s+stock",
        r"(\d+)\s+left\s+in\s+stock",
        r"(\d+)\s+units?\s+available",
        r"stock\s*[:\-]\s*(\d+)",
        r"quantity\s*[:\-]\s*(\d+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)

        if match:
            try:
                return int(match.group(1))
            except Exception:
                pass

    return None


def determine_availability(text):
    """
    Conservative stock detection.

    Returns:
        True  = available
        False = unavailable
    """

    t = text.lower()

    # Definitely unavailable
    unavailable = [
        "out of stock",
        "sold out",
        "currently unavailable",
        "not available",
        "unavailable",
        "coming soon",
    ]

    for phrase in unavailable:
        if phrase in t:
            return False

    # Definitely available
    available = [
        "in stock",
        "add to cart",
        "buy now",
        "available",
    ]

    for phrase in available:
        if phrase in t:
            return True

    # If this is a product card and has no negative status,
    # treat it as available.
    return True


def extract_series(title):
    keywords = [
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

    for keyword in keywords:
        if re.search(
            rf"\b{re.escape(keyword)}\b",
            title,
            re.IGNORECASE,
        ):
            return keyword

    return "General"


def extract_image(card, base_url):
    img = card.find("img")

    if not img:
        return ""

    candidates = [
        img.get("src"),
        img.get("data-src"),
        img.get("data-original"),
        img.get("data-lazy-src"),
    ]

    srcset = img.get("srcset")

    if srcset:
        first = srcset.split(",")[0].strip().split(" ")[0]
        candidates.append(first)

    for src in candidates:
        if src:
            return absolute_url(base_url, src)

    return ""


# ============================================================
# PRODUCT TITLE EXTRACTION
# ============================================================

def extract_title(card, link=None):
    """
    Try several common product-card title formats.
    """

    # First try heading elements
    for tag in ["h1", "h2", "h3", "h4", "h5"]:
        heading = card.find(tag)

        if heading:
            text = clean_text(
                heading.get_text(" ", strip=True)
            )

            if text and len(text) > 2:
                return text

    # Common title classes
    title_selectors = [
        ".product-title",
        ".product-name",
        ".product__title",
        ".card-title",
        ".card__heading",
        ".product-card-title",
        ".product-item-title",
        ".name",
        ".title",
    ]

    for selector in title_selectors:
        element = card.select_one(selector)

        if element:
            text = clean_text(
                element.get_text(" ", strip=True)
            )

            if text and len(text) > 2:
                return text

    # Link text
    if link:
        text = clean_text(
            link.get_text(" ", strip=True)
        )

        if text and len(text) > 2:
            return text

    return ""


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_alert(item, alert_type):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("ERROR: Telegram secrets are missing.")
        return False

    title = html.escape(item["title"])
    price = html.escape(str(item["price"]))
    series = html.escape(item["series"])
    source = html.escape(item["source"])
    buy_url = html.escape(
        item["buy_url"],
        quote=True,
    )

    quantity = item.get("quantity")

    if quantity is not None:
        quantity_text = (
            f"📦 <b>Units:</b> {quantity}\n"
        )
    else:
        quantity_text = (
            "📦 <b>Units:</b> Not exposed\n"
        )

    if alert_type == "RESTOCK":
        heading = "🚨 <b>HMT RESTOCK ALERT</b>"
    elif alert_type == "NEW":
        heading = "🆕 <b>NEW HMT WATCH ALERT</b>"
    else:
        heading = "📦 <b>HMT STOCK ALERT</b>"

    detected = datetime.now().strftime(
        "%d %b %Y, %I:%M:%S %p"
    )

    caption = (
        f"{heading}\n\n"
        f"⌚ <b>Product:</b> {title}\n"
        f"💰 <b>Price:</b> ₹{price}\n"
        f"🏷️ <b>Series:</b> {series}\n"
        f"🌐 <b>Source:</b> {source}\n"
        f"{quantity_text}"
        f"🕒 <b>Detected:</b> {detected}\n\n"
        f"🛒 <a href='{buy_url}'>Open product</a>"
    )

    image_url = item.get("image_url")

    # --------------------------------------------------------
    # PHOTO
    # --------------------------------------------------------

    if image_url:

        api_url = (
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
                api_url,
                data=payload,
                timeout=15,
            )

            if response.ok:
                print(
                    f"Telegram alert sent: "
                    f"{item['title']}"
                )
                return True

            print(
                "sendPhoto failed: "
                f"{response.status_code} "
                f"{response.text[:300]}"
            )

        except Exception as e:
            print(f"sendPhoto error: {e}")

    # --------------------------------------------------------
    # TEXT FALLBACK
    # --------------------------------------------------------

    api_url = (
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
            api_url,
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
            "sendMessage failed: "
            f"{response.status_code} "
            f"{response.text[:300]}"
        )

    except Exception as e:
        print(f"sendMessage error: {e}")

    return False


# ============================================================
# HMT STORE
# ============================================================

def scrape_hmt_store():

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
            return catalog

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        # ----------------------------------------------------
        # Find likely product links.
        #
        # The Store is NOT Shopify-style /products/.
        # We therefore use several possible patterns.
        # ----------------------------------------------------

        candidates = []

        for a in soup.find_all("a", href=True):

            href = a.get("href", "").strip()
            text = clean_text(
                a.get_text(" ", strip=True)
            )

            if not href:
                continue

            href_lower = href.lower()

            is_product_link = (
                "/product/" in href_lower
                or "/products/" in href_lower
                or "/product?" in href_lower
                or "/product/" in href_lower
            )

            # Product links usually have useful title text.
            if is_product_link and text:
                candidates.append(a)

        # If normal link detection fails, inspect elements
        # containing price/product-like text.
        if not candidates:

            for element in soup.find_all(
                ["article", "li", "div"]
            ):

                text = clean_text(
                    element.get_text(
                        " ",
                        strip=True,
                    )
                )

                if "₹" not in text and "Rs." not in text:
                    continue

                link = element.find(
                    "a",
                    href=True,
                )

                if link:
                    candidates.append(link)

        # Deduplicate URLs
        unique = {}

        for a in candidates:

            href = a.get("href", "")

            product_url = absolute_url(
                HMT_STORE_URL,
                href,
            )

            if product_url:
                unique[product_url] = a

        print(
            f"HMT Store candidate products: "
            f"{len(unique)}"
        )

        for product_url, link in unique.items():

            # Get a larger surrounding card.
            card = link

            for _ in range(4):

                parent = card.parent

                if parent is None:
                    break

                parent_text = clean_text(
                    parent.get_text(
                        " ",
                        strip=True,
                    )
                )

                # A product card normally contains a price.
                if (
                    "₹" in parent_text
                    or "Rs." in parent_text
                    or "RS." in parent_text
                ):
                    card = parent
                    break

                card = parent

            title = extract_title(
                card,
                link,
            )

            if not title:
                continue

            # Remove generic navigation links.
            if len(title) < 4:
                continue

            if is_excluded(title):
                continue

            card_text = clean_text(
                card.get_text(
                    " ",
                    strip=True,
                )
            )

            price = normalize_price(
                card_text
            )

            available = determine_availability(
                card_text
            )

            quantity = extract_quantity(
                card_text
            )

            image_url = extract_image(
                card,
                HMT_STORE_URL,
            )

            product_id = (
                "store:"
                + product_url.rstrip("/").lower()
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
        print(
            f"HMT Store scraping error: {e}"
        )

    print(
        f"HMT Store usable products: "
        f"{len(catalog)}"
    )

    return catalog


# ============================================================
# HMT.IN
# ============================================================

def scrape_hmt_in():

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
            return catalog

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        # ----------------------------------------------------
        # HMT.IN PRODUCT LINKS
        #
        # The site uses product_overview URLs.
        # ----------------------------------------------------

        candidates = []

        for a in soup.find_all(
            "a",
            href=True,
        ):

            href = a.get("href", "")
            text = clean_text(
                a.get_text(
                    " ",
                    strip=True,
                )
            )

            href_lower = href.lower()

            if (
                "product_overview" in href_lower
                and text
            ):
                candidates.append(a)

        print(
            f"HMT.in product links found: "
            f"{len(candidates)}"
        )

        # ----------------------------------------------------
        # DEDUPLICATE BY PRODUCT URL
        # ----------------------------------------------------

        unique = {}

        for a in candidates:

            product_url = absolute_url(
                HMT_IN_URL,
                a.get("href", ""),
            )

            if product_url:
                unique[product_url] = a

        print(
            f"HMT.in unique product URLs: "
            f"{len(unique)}"
        )

        for product_url, link in unique.items():

            card = link

            # Find the nearest card containing price/status.
            for _ in range(5):

                parent = card.parent

                if parent is None:
                    break

                parent_text = clean_text(
                    parent.get_text(
                        " ",
                        strip=True,
                    )
                )

                if (
                    "RS." in parent_text.upper()
                    or "₹" in parent_text
                    or "OUT OF STOCK" in parent_text.upper()
                    or "COMING SOON" in parent_text.upper()
                ):
                    card = parent
                    break

                card = parent

            title = extract_title(
                card,
                link,
            )

            if not title:
                continue

            if is_excluded(title):
                continue

            card_text = clean_text(
                card.get_text(
                    " ",
                    strip=True,
                )
            )

            price = normalize_price(
                card_text
            )

            available = determine_availability(
                card_text
            )

            quantity = extract_quantity(
                card_text
            )

            image_url = extract_image(
                card,
                HMT_IN_URL,
            )

            product_id = (
                "in:"
                + product_url.lower()
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
        print(
            f"HMT.in scraping error: {e}"
        )

    print(
        f"HMT.in usable products: "
        f"{len(catalog)}"
    )

    return catalog


# ============================================================
# SCRAPE BOTH SITES
# ============================================================

def scrape_all():

    store = scrape_hmt_store()
    hmt_in = scrape_hmt_in()

    combined = {}

    combined.update(store)
    combined.update(hmt_in)

    print()
    print("=" * 42)
    print("SCRAPE SUMMARY")
    print("=" * 42)

    print(
        f"HMT Store: {len(store)}"
    )

    print(
        f"HMT.in:    {len(hmt_in)}"
    )

    print(
        f"TOTAL:     {len(combined)}"
    )

    return combined


# ============================================================
# STATE
# ============================================================

def load_previous_state():

    if not os.path.exists(STATE_FILE):
        return {}

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8",
        ) as f:

            state = json.load(f)

        if not isinstance(state, dict):
            return {}

        return state

    except Exception as e:

        print(
            f"Error loading state: {e}"
        )

        return {}


def save_state(state):

    with open(
        STATE_FILE,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            state,
            f,
            indent=2,
            ensure_ascii=False,
        )


def old_state_format(state):

    if not state:
        return False

    # Old version used:
    # {"available": ..., "title": ...}
    for value in state.values():

        if not isinstance(value, dict):
            return True

        if "source" not in value:
            return True

        if "url" not in value:
            return True

    return False


# ============================================================
# PROCESS STOCK
# ============================================================

def process_stock(
    current,
    previous,
):

    updated = {}

    restocks = 0
    new_alerts = 0
    quantity_changes = 0

    for product_id, item in current.items():

        old = previous.get(
            product_id
        )

        # ----------------------------------------------------
        # NEW PRODUCT
        # ----------------------------------------------------

        if old is None:

            print(
                f"NEW BASELINE: "
                f"{item['title'][:45]} "
                f"[{item['source']}]"
            )

            updated[product_id] = {
                "title": item["title"],
                "price": item["price"],
                "available": item["available"],
                "quantity": item["quantity"],
                "source": item["source"],
                "url": item["buy_url"],
            }

            continue

        old_available = old.get(
            "available",
            False,
        )

        old_quantity = old.get(
            "quantity"
        )

        new_available = item[
            "available"
        ]

        new_quantity = item[
            "quantity"
        ]

        # ----------------------------------------------------
        # RESTOCK
        # ----------------------------------------------------

        if (
            new_available
            and not old_available
        ):

            print(
                f"🚨 RESTOCK DETECTED: "
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
            old_quantity is not None
            and new_quantity is not None
            and old_quantity != new_quantity
        ):

            print(
                f"QUANTITY CHANGE: "
                f"{item['title']} "
                f"{old_quantity} -> "
                f"{new_quantity}"
            )

            quantity_changes += 1

        # ----------------------------------------------------
        # SAVE
        # ----------------------------------------------------

        updated[product_id] = {
            "title": item["title"],
            "price": item["price"],
            "available": new_available,
            "quantity": new_quantity,
            "source": item["source"],
            "url": item["buy_url"],
        }

    return (
        updated,
        new_alerts,
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
    # OLD STATE MIGRATION
    # --------------------------------------------------------

    if old_state_format(previous_state):

        print()
        print(
            "OLD STATE FORMAT DETECTED."
        )

        print(
            "Creating clean baseline."
        )

        previous_state = {}

    # --------------------------------------------------------
    # SCRAPE
    # --------------------------------------------------------

    current_catalog = scrape_all()

    print()
    print(
        f"Processing "
        f"{len(current_catalog)} products..."
    )

    # NEVER destroy state if scraper suddenly returns 0.
    if not current_catalog:

        print()
        print(
            "WARNING: ZERO PRODUCTS FOUND."
        )

        print(
            "Existing stock state was NOT overwritten."
        )

        return

    # --------------------------------------------------------
    # PROCESS
    # --------------------------------------------------------

    (
        updated_state,
        new_alerts,
        restocks,
        quantity_changes,
    ) = process_stock(
        current_catalog,
        previous_state,
    )

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    save_state(updated_state)

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
