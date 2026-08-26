from datetime import datetime
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
    "Cache-Control": "no-cache",
}


# ============================================================
# FILTERS
# ============================================================

def is_excluded(title):
    t = title.lower()

    # User requested these to be ignored.
    if re.search(r"\bgalaxy\b", t):
        return True

    if re.search(r"\binox\b", t):
        return True

    return False


# ============================================================
# HELPERS
# ============================================================

def clean_text(text):
    if not text:
        return ""

    return re.sub(r"\s+", " ", text).strip()


def absolute_url(base, href):
    if not href:
        return ""

    href = href.strip()

    if href.startswith("//"):
        return "https:" + href

    return urljoin(base, href)


def extract_price(text):
    if not text:
        return "N/A"

    patterns = [
        r"₹\s*([\d,]+)",
        r"Rs\.?\s*([\d,]+)",
        r"RS\.?\s*([\d,]+)",
        r"INR\s*([\d,]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.I)

        if match:
            return match.group(1).replace(",", "")

    return "N/A"


def extract_quantity(text):
    """
    Try to extract an actual stock quantity when the site exposes it.

    Examples:
        Only 3 left
        Only 3 left in stock
        3 units available
        Stock: 3
        Quantity: 3
    """

    if not text:
        return None

    patterns = [
        r"only\s+(\d+)\s+left\s+in\s+stock",
        r"only\s+(\d+)\s+left",
        r"(\d+)\s+left\s+in\s+stock",
        r"(\d+)\s+units?\s+available",
        r"(\d+)\s+items?\s+available",
        r"stock\s*[:\-]\s*(\d+)",
        r"quantity\s*[:\-]\s*(\d+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.I)

        if match:
            try:
                return int(match.group(1))
            except Exception:
                pass

    return None


def detect_available(text):
    """
    Conservative availability detection.

    Explicit unavailable statuses always win.
    """

    t = text.lower()

    unavailable = [
        "out of stock",
        "sold out",
        "coming soon",
        "currently unavailable",
        "not available",
        "unavailable",
    ]

    for phrase in unavailable:
        if phrase in t:
            return False

    available = [
        "in stock",
        "add to cart",
        "buy now",
        "available",
    ]

    for phrase in available:
        if phrase in t:
            return True

    # Product cards with no unavailable marker are assumed
    # available. This is useful for catalogue pages where
    # stock is represented by the absence of "out of stock".
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
        "Jhalak",
        "Kedar",
        "Kanchan",
        "Rajat",
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
            re.I,
        ):
            return keyword

    return "General"


def extract_image(element, base_url):
    img = element.find("img")

    if not img:
        return ""

    candidates = [
        img.get("src"),
        img.get("data-src"),
        img.get("data-lazy-src"),
        img.get("data-original"),
    ]

    srcset = img.get("srcset")

    if srcset:
        first = srcset.split(",")[0].strip().split(" ")[0]
        candidates.append(first)

    for src in candidates:
        if src:
            return absolute_url(base_url, src)

    return ""


def extract_title(element, link=None):
    """
    Try several common product-card structures.
    """

    selectors = [
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
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

    for selector in selectors:
        found = element.select_one(selector)

        if found:
            text = clean_text(
                found.get_text(" ", strip=True)
            )

            if 3 <= len(text) <= 200:
                return text

    if link:
        text = clean_text(
            link.get_text(" ", strip=True)
        )

        if 3 <= len(text) <= 200:
            return text

    return ""


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(item, alert_type):

    if not TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN missing.")
        return False

    if not TELEGRAM_CHAT_ID:
        print("ERROR: TELEGRAM_CHAT_ID missing.")
        return False

    title = item["title"]
    price = item.get("price", "N/A")
    series = item.get("series", "General")
    source = item.get("source", "")
    buy_url = item.get("buy_url", "")
    quantity = item.get("quantity")

    if alert_type == "RESTOCK":
        heading = "🚨 HMT RESTOCK ALERT"
    elif alert_type == "NEW":
        heading = "🆕 NEW HMT WATCH"
    elif alert_type == "QUANTITY":
        heading = "📦 HMT STOCK CHANGE"
    else:
        heading = "⌚ HMT STOCK ALERT"

    if quantity is None:
        quantity_line = "📦 <b>Units:</b> Not exposed"
    else:
        quantity_line = (
            f"📦 <b>Units:</b> {quantity}"
        )

    detected = datetime.now().strftime(
        "%d %b %Y, %I:%M:%S %p"
    )

    caption = (
        f"<b>{heading}</b>\n\n"
        f"⌚ <b>Product:</b> {title}\n"
        f"💰 <b>Price:</b> ₹{price}\n"
        f"🏷️ <b>Series:</b> {series}\n"
        f"🌐 <b>Source:</b> {source}\n"
        f"{quantity_line}\n"
        f"🕒 <b>Detected:</b> {detected}\n\n"
        f"🛒 <a href=\"{buy_url}\">Open product</a>"
    )

    # --------------------------------------------------------
    # Try photo first
    # --------------------------------------------------------

    image_url = item.get("image_url", "")

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
                    f"{title}"
                )
                return True

            print(
                "Telegram photo failed: "
                f"{response.status_code}"
            )

        except Exception as e:
            print(
                f"Telegram photo error: {e}"
            )

    # --------------------------------------------------------
    # Text fallback
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
                f"{title}"
            )
            return True

        print(
            "Telegram message failed: "
            f"{response.status_code}"
        )

    except Exception as e:
        print(
            f"Telegram message error: {e}"
        )

    return False


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
            timeout=25,
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

        links = []

        for a in soup.find_all(
            "a",
            href=True,
        ):

            href = a.get("href", "")

            if "product_overview" not in href.lower():
                continue

            links.append(a)

        print(
            f"HMT.in product links found: "
            f"{len(links)}"
        )

        # ----------------------------------------------------
        # Unique product URLs
        # ----------------------------------------------------

        unique_links = {}

        for a in links:

            href = a.get("href", "")

            product_url = absolute_url(
                HMT_IN_URL,
                href,
            )

            if product_url:
                unique_links[product_url] = a

        print(
            f"HMT.in unique product URLs: "
            f"{len(unique_links)}"
        )

        # ----------------------------------------------------
        # Extract products
        # ----------------------------------------------------

        for product_url, link in unique_links.items():

            card = link

            # Move upward until we find a useful card.
            for _ in range(6):

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
                    "₹" in parent_text
                    or "RS." in parent_text.upper()
                    or "OUT OF STOCK"
                    in parent_text.upper()
                    or "COMING SOON"
                    in parent_text.upper()
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

            price = extract_price(
                card_text
            )

            available = detect_available(
                card_text
            )

            quantity = extract_quantity(
                card_text
            )

            image_url = extract_image(
                card,
                HMT_IN_URL,
            )

            # URL is the most reliable product identity.
            product_id = (
                "hmt-in:"
                + product_url
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
            timeout=25,
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
        # IMPORTANT:
        #
        # The .store website has changed its HTML structure
        # multiple times. Rather than relying on one CSS class,
        # inspect every link and identify likely product URLs.
        # ----------------------------------------------------

        candidate_links = []

        for a in soup.find_all(
            "a",
            href=True,
        ):

            href = a.get("href", "").strip()

            if not href:
                continue

            href_lower = href.lower()

            # Common product URL patterns.
            if any(
                pattern in href_lower
                for pattern in [
                    "/products/",
                    "/product/",
                    "/product?",
                    "/collections/",
                ]
            ):

                # Don't treat the collection page itself
                # as a product.
                if href_lower.rstrip("/") in [
                    "/collections/all",
                    "/all-products",
                ]:
                    continue

                candidate_links.append(a)

        # ----------------------------------------------------
        # If no obvious product links were found, look for
        # links with watch-like text and a price nearby.
        # ----------------------------------------------------

        if not candidate_links:

            for a in soup.find_all(
                "a",
                href=True,
            ):

                text = clean_text(
                    a.get_text(
                        " ",
                        strip=True,
                    )
                )

                if len(text) < 5:
                    continue

                parent = a.parent

                if parent is None:
                    continue

                parent_text = clean_text(
                    parent.get_text(
                        " ",
                        strip=True,
                    )
                )

                if (
                    "₹" in parent_text
                    or "Rs." in parent_text
                    or "RS." in parent_text
                ):

                    candidate_links.append(a)

        print(
            f"HMT Store candidate links: "
            f"{len(candidate_links)}"
        )

        # ----------------------------------------------------
        # Unique URLs
        # ----------------------------------------------------

        unique_links = {}

        for a in candidate_links:

            href = a.get(
                "href",
                "",
            )

            product_url = absolute_url(
                HMT_STORE_URL,
                href,
            )

            if not product_url:
                continue

            # Don't include obvious navigation.
            if product_url.rstrip("/") in [
                HMT_STORE_URL.rstrip("/"),
                "https://www.hmtwatches.store/",
            ]:
                continue

            unique_links[product_url] = a

        print(
            f"HMT Store unique URLs: "
            f"{len(unique_links)}"
        )

        # ----------------------------------------------------
        # Extract products
        # ----------------------------------------------------

        for product_url, link in unique_links.items():

            card = link

            # Walk up the DOM to find the product card.
            for _ in range(6):

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
                    "₹" in parent_text
                    or "RS." in parent_text.upper()
                    or "SOLD OUT" in parent_text.upper()
                    or "OUT OF STOCK"
                    in parent_text.upper()
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

            # Ignore obvious navigation.
            bad_titles = [
                "home",
                "shop",
                "all products",
                "collections",
                "contact",
                "about",
                "search",
                "cart",
                "login",
            ]

            if title.lower() in bad_titles:
                continue

            card_text = clean_text(
                card.get_text(
                    " ",
                    strip=True,
                )
            )

            price = extract_price(
                card_text
            )

            available = detect_available(
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
                "hmt-store:"
                + product_url.rstrip("/")
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
# COMBINE
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

def load_state():

    if not os.path.exists(STATE_FILE):
        return {}

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8",
        ) as f:

            data = json.load(f)

        if not isinstance(data, dict):
            return {}

        return data

    except Exception as e:

        print(
            f"State loading error: {e}"
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


def is_old_state(state):

    if not state:
        return False

    for value in state.values():

        if not isinstance(value, dict):
            return True

        # Current format contains source + url.
        if (
            "source" not in value
            or "url" not in value
        ):
            return True

    return False


# ============================================================
# PROCESS
# ============================================================

def process_products(
    current,
    previous,
):

    updated = {}

    restocks = 0
    new_products = 0
    quantity_changes = 0

    for product_id, item in current.items():

        old = previous.get(
            product_id
        )

        # ----------------------------------------------------
        # FIRST TIME SEEING PRODUCT
        #
        # IMPORTANT:
        # Do NOT alert here.
        # This creates the baseline.
        # ----------------------------------------------------

        if old is None:

            print(
                f"BASELINE: "
                f"{item['title'][:55]} "
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

            print()
            print(
                "🚨 RESTOCK DETECTED"
            )

            print(
                f"Product: "
                f"{item['title']}"
            )

            print(
                f"Source: "
                f"{item['source']}"
            )

            if telegram_send(
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

            # Only alert quantity increases.
            # A decrease is useful for logging but doesn't
            # need a Telegram alert.
            if new_quantity > old_quantity:

                if telegram_send(
                    item,
                    "QUANTITY",
                ):
                    quantity_changes += 1

        # ----------------------------------------------------
        # UPDATE STATE
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

    previous = load_state()

    # --------------------------------------------------------
    # Old state format
    # --------------------------------------------------------

    if is_old_state(previous):

        print()
        print(
            "OLD STATE FORMAT DETECTED."
        )

        print(
            "Creating clean baseline."
        )

        previous = {}

    # --------------------------------------------------------
    # SCRAPE
    # --------------------------------------------------------

    current = scrape_all()

    print()
    print(
        f"Processing "
        f"{len(current)} products..."
    )

    # --------------------------------------------------------
    # SAFETY:
    #
    # Never erase a working state because a website failed,
    # blocked GitHub, changed HTML, or temporarily returned
    # an empty page.
    # --------------------------------------------------------

    if len(current) == 0:

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
        updated,
        new_products,
        restocks,
        quantity_changes,
    ) = process_products(
        current,
        previous,
    )

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    save_state(updated)

    print()
    print(
        f"Saved {len(updated)} products "
        f"to {STATE_FILE}"
    )

    print()
    print("=" * 60)
    print("RUN COMPLETE")
    print("=" * 60)

    print(
        f"Products monitored: "
        f"{len(updated)}"
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
