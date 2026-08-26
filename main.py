from datetime import datetime
import json
import os
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

# ============================================================
# CONFIG
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

STATE_FILE = "stock_state.json"

HMT_STORE_URL = "https://www.hmtwatches.store/collections/all"
HMT_IN_URL = "https://www.hmtwatches.in/all_product"

HMT_STORE_BASE = "https://www.hmtwatches.store"
HMT_IN_BASE = "https://www.hmtwatches.in"

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

# User requested these to be ignored.
EXCLUDED_KEYWORDS = [
    "galaxy",
    "inox",
]

# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()
session.headers.update(HEADERS)


# ============================================================
# HELPERS
# ============================================================

def clean_text(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def is_excluded(title):
    title_lower = title.lower()

    for keyword in EXCLUDED_KEYWORDS:
        if re.search(rf"\b{re.escape(keyword)}\b", title_lower):
            return True

    return False


def make_product_id(source, title, url):
    """
    URL is preferred because HMT.in can have multiple watches
    with very similar titles.
    """
    raw = f"{source}|{url}|{title}".lower()

    return re.sub(r"[^a-z0-9]+", "_", raw).strip("_")


def extract_series(title):
    series_keywords = [
        "Pace",
        "Plus",
        "Vihaan",
        "Elegance",
        "Sangam",
        "Stellar",
        "Tareeq",
        "Janata",
        "Pilot",
        "Vijay",
        "Sona",
        "Ravi",
        "Kohinoor",
        "Commando",
        "Jawan",
        "Vivek",
        "Bahadur",
        "Gandaberunda",
        "Operation Sindoor",
        "Chronograph",
        "Automatic",
        "Quartz",
    ]

    for key in series_keywords:
        if re.search(rf"\b{re.escape(key)}\b", title, re.IGNORECASE):
            return key

    return "General"


def extract_price(text):
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
    Attempts to detect explicit stock quantity.

    Examples handled:
    - 5 in stock
    - Only 3 left
    - 3 items available
    - Available quantity: 4

    If the website does not expose an actual quantity,
    returns None rather than guessing.
    """

    patterns = [
        r"only\s+(\d+)\s+(?:left|remaining)",
        r"(\d+)\s+(?:units?|items?)\s+(?:left|remaining|available)",
        r"(\d+)\s+in\s+stock",
        r"available\s*[:\-]?\s*(\d+)",
        r"stock\s*[:\-]?\s*(\d+)",
        r"quantity\s*[:\-]?\s*(\d+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)

        if match:
            try:
                return int(match.group(1))
            except Exception:
                pass

    return None


def detect_availability(text):
    """
    Conservative availability detection.

    Explicit out-of-stock language wins.

    Explicit purchase language is considered available.
    """

    text_lower = text.lower()

    out_patterns = [
        "out of stock",
        "sold out",
        "currently unavailable",
        "not available",
    ]

    for phrase in out_patterns:
        if phrase in text_lower:
            return False

    in_patterns = [
        "add to cart",
        "buy now",
        "add to wishlist",
        "available",
        "in stock",
    ]

    for phrase in in_patterns:
        if phrase in text_lower:
            return True

    # If no explicit signal exists, don't claim availability.
    return False


def extract_image(soup):
    img = soup.find("img")

    if not img:
        return ""

    for attribute in ["src", "data-src", "data-original"]:
        value = img.get(attribute)

        if value:
            return urljoin(HMT_IN_BASE, value)

    srcset = img.get("srcset")

    if srcset:
        first = srcset.split(",")[0].strip().split(" ")[0]

        if first:
            return urljoin(HMT_IN_BASE, first)

    return ""


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_alert(item, alert_type="RESTOCK"):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("WARNING: Telegram secrets are missing.")
        return False

    quantity_text = (
        str(item["quantity"])
        if item.get("quantity") is not None
        else "Not exposed by website"
    )

    caption = (
        f"🚨 <b>HMT STOCK ALERT</b>\n\n"
        f"📦 <b>{item['title']}</b>\n"
        f"🏷️ <b>Source:</b> {item['source']}\n"
        f"💰 <b>Price:</b> ₹{item['price']}\n"
        f"⌚ <b>Series:</b> {item['series']}\n"
        f"📊 <b>Stock:</b> {quantity_text}\n"
        f"🔔 <b>Event:</b> {alert_type}\n"
        f"🕒 <b>Detected:</b> "
        f"{datetime.now().strftime('%d %b %Y, %I:%M:%S %p')}\n\n"
        f"🌐 <a href='{item['url']}'>Open Product</a>"
    )

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
        response = session.post(
            api_url,
            data=payload,
            timeout=15,
        )

        print(
            f"Telegram response: "
            f"{response.status_code} - {response.text[:300]}"
        )

        return response.ok

    except Exception as e:
        print(f"Telegram error: {e}")
        return False


# ============================================================
# STATE
# ============================================================

def load_state():
    if not os.path.exists(STATE_FILE):
        return {}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)

        if isinstance(state, dict):
            return state

    except Exception as e:
        print(f"Could not load state: {e}")

    return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            state,
            f,
            indent=2,
            ensure_ascii=False,
        )


# ============================================================
# HMT STORE
# ============================================================

def scrape_hmt_store():
    print("=" * 42)
    print("SCRAPING HMT STORE")
    print("=" * 42)

    products = {}

    try:
        response = session.get(
            HMT_STORE_URL,
            timeout=30,
        )

        print(
            f"HMT Store HTTP status: "
            f"{response.status_code}"
        )

        if response.status_code != 200:
            return products

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        # The HMT Store is not a normal Shopify page.
        # Look for product-like records using multiple strategies.

        candidates = []

        # Product/article/card elements
        candidates.extend(
            soup.find_all(
                [
                    "article",
                    "li",
                    "div",
                ]
            )
        )

        seen_urls = set()

        for element in candidates:
            link = element.find(
                "a",
                href=True,
            )

            if not link:
                continue

            href = link.get("href", "").strip()

            if not href:
                continue

            absolute_url = urljoin(
                HMT_STORE_BASE,
                href,
            )

            parsed = urlparse(absolute_url)

            # Only product-looking URLs
            path_lower = parsed.path.lower()

            if not any(
                x in path_lower
                for x in [
                    "/product",
                    "/products/",
                    "/item",
                ]
            ):
                continue

            if absolute_url in seen_urls:
                continue

            seen_urls.add(absolute_url)

            text = clean_text(
                element.get_text(
                    " ",
                    strip=True,
                )
            )

            if len(text) < 10:
                continue

            # Find likely title
            title = ""

            for tag in [
                "h1",
                "h2",
                "h3",
                "h4",
                "strong",
                "p",
            ]:
                found = element.find(tag)

                if found:
                    candidate_title = clean_text(
                        found.get_text(
                            " ",
                            strip=True,
                        )
                    )

                    if (
                        3
                        <= len(candidate_title)
                        <= 150
                        and "₹" not in candidate_title
                    ):
                        title = candidate_title
                        break

            if not title:
                continue

            if is_excluded(title):
                continue

            price = extract_price(text)
            quantity = extract_quantity(text)
            available = detect_availability(text)

            image = extract_image(element)

            product_id = make_product_id(
                "HMT Store",
                title,
                absolute_url,
            )

            products[product_id] = {
                "title": title,
                "price": price,
                "series": extract_series(title),
                "image_url": image,
                "url": absolute_url,
                "source": "HMT Store",
                "available": available,
                "quantity": quantity,
            }

        print(
            f"HMT Store candidate products: "
            f"{len(seen_urls)}"
        )

        print(
            f"HMT Store usable products: "
            f"{len(products)}"
        )

    except Exception as e:
        print(
            f"HMT Store scraping error: {e}"
        )

    return products


# ============================================================
# HMT.IN
# ============================================================

def get_hmt_in_product_urls():
    """
    HMT.in uses:
        /all_product

    Product detail pages use:
        /product_details?id=...

    This is why looking for /products/ returns zero.
    """

    urls = set()

    try:
        response = session.get(
            HMT_IN_URL,
            timeout=30,
        )

        print(
            f"HMT.in HTTP status: "
            f"{response.status_code}"
        )

        if response.status_code != 200:
            return urls

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        for link in soup.find_all(
            "a",
            href=True,
        ):
            href = link.get("href", "").strip()

            if "product_details" not in href.lower():
                continue

            absolute_url = urljoin(
                HMT_IN_BASE,
                href,
            )

            urls.add(absolute_url)

        print(
            f"HMT.in product links found: "
            f"{len(urls)}"
        )

    except Exception as e:
        print(
            f"HMT.in URL discovery error: {e}"
        )

    return urls


def scrape_hmt_in():
    print("=" * 42)
    print("SCRAPING HMT.IN")
    print("=" * 42)

    products = {}

    product_urls = get_hmt_in_product_urls()

    print(
        f"HMT.in unique product URLs: "
        f"{len(product_urls)}"
    )

    for index, url in enumerate(
        product_urls,
        start=1,
    ):
        try:
            response = session.get(
                url,
                timeout=20,
            )

            if response.status_code != 200:
                continue

            soup = BeautifulSoup(
                response.text,
                "html.parser",
            )

            page_text = clean_text(
                soup.get_text(
                    " ",
                    strip=True,
                )
            )

            # ------------------------------------------------
            # TITLE
            # ------------------------------------------------

            title = ""

            # First try common headings
            for tag in [
                "h1",
                "h2",
                "h3",
            ]:
                elements = soup.find_all(tag)

                for element in elements:
                    candidate = clean_text(
                        element.get_text(
                            " ",
                            strip=True,
                        )
                    )

                    if (
                        candidate
                        and len(candidate) >= 4
                        and len(candidate) <= 150
                        and "HMT" in candidate.upper()
                    ):
                        title = candidate
                        break

                if title:
                    break

            # Search meta title
            if not title:
                meta = soup.find(
                    "meta",
                    property="og:title",
                )

                if meta:
                    title = clean_text(
                        meta.get("content", "")
                    )

            # Search document title
            if not title and soup.title:
                title = clean_text(
                    soup.title.get_text()
                )

            if not title:
                continue

            # Clean common suffixes
            title = re.sub(
                r"\s*\|\s*HMT.*$",
                "",
                title,
                flags=re.IGNORECASE,
            )

            title = clean_text(title)

            if len(title) < 4:
                continue

            # ------------------------------------------------
            # EXCLUDE GALAXY / INOX
            # ------------------------------------------------

            if is_excluded(title):
                continue

            # ------------------------------------------------
            # PRICE
            # ------------------------------------------------

            price = extract_price(page_text)

            # ------------------------------------------------
            # STOCK
            # ------------------------------------------------

            quantity = extract_quantity(page_text)

            available = detect_availability(
                page_text
            )

            # ------------------------------------------------
            # IMAGE
            # ------------------------------------------------

            image = ""

            og_image = soup.find(
                "meta",
                property="og:image",
            )

            if og_image:
                image = urljoin(
                    HMT_IN_BASE,
                    og_image.get(
                        "content",
                        "",
                    ),
                )

            if not image:
                image = extract_image(soup)

            # ------------------------------------------------
            # SAVE
            # ------------------------------------------------

            product_id = make_product_id(
                "HMT.in",
                title,
                url,
            )

            products[product_id] = {
                "title": title,
                "price": price,
                "series": extract_series(title),
                "image_url": image,
                "url": url,
                "source": "HMT.in",
                "available": available,
                "quantity": quantity,
            }

        except Exception as e:
            print(
                f"HMT.in product error "
                f"{index}/{len(product_urls)}: "
                f"{e}"
            )

    print(
        f"HMT.in usable products: "
        f"{len(products)}"
    )

    return products


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)
    print("HMT WATCH RESTOCK TRACKER")
    print("=" * 60)

    previous_state = load_state()

    store_products = scrape_hmt_store()
    hmt_products = scrape_hmt_in()

    current_products = {}

    current_products.update(store_products)
    current_products.update(hmt_products)

    print()
    print("=" * 42)
    print("SCRAPE SUMMARY")
    print("=" * 42)

    print(
        f"HMT Store: {len(store_products)}"
    )

    print(
        f"HMT.in:    {len(hmt_products)}"
    )

    print(
        f"TOTAL:     {len(current_products)}"
    )

    # --------------------------------------------------------
    # SAFETY
    # --------------------------------------------------------

    if len(current_products) == 0:
        print()
        print(
            "WARNING: ZERO PRODUCTS FOUND."
        )
        print(
            "Existing stock_state.json "
            "will NOT be overwritten."
        )
        return

    # If one website suddenly disappears, don't erase
    # everything from the previous run.
    if (
        len(store_products) == 0
        and len(hmt_products) == 0
    ):
        print(
            "WARNING: No products from either site."
        )
        return

    print()
    print(
        f"Processing {len(current_products)} products..."
    )

    updated_state = {}

    restocks = 0
    quantity_changes = 0
    new_products = 0

    for product_id, item in current_products.items():

        previous = previous_state.get(
            product_id
        )

        # ----------------------------------------------------
        # NEW PRODUCT
        # ----------------------------------------------------

        if previous is None:

            print(
                f"BASELINE: "
                f"{item['title']} "
                f"[{item['source']}]"
            )

            new_products += 1

        else:

            was_available = bool(
                previous.get(
                    "available",
                    False,
                )
            )

            is_available = bool(
                item.get(
                    "available",
                    False,
                )
            )

            old_quantity = previous.get(
                "quantity"
            )

            new_quantity = item.get(
                "quantity"
            )

            # ------------------------------------------------
            # RESTOCK
            # ------------------------------------------------

            if (
                is_available
                and not was_available
            ):

                print(
                    f"RESTOCK DETECTED: "
                    f"{item['title']} "
                    f"[{item['source']}]"
                )

                if send_telegram_alert(
                    item,
                    "RESTOCK",
                ):
                    restocks += 1

            # ------------------------------------------------
            # QUANTITY INCREASE
            # ------------------------------------------------

            elif (
                old_quantity is not None
                and new_quantity is not None
                and new_quantity > old_quantity
            ):

                print(
                    f"QUANTITY INCREASE: "
                    f"{item['title']} "
                    f"{old_quantity} -> "
                    f"{new_quantity}"
                )

                if send_telegram_alert(
                    item,
                    "STOCK INCREASE",
                ):
                    quantity_changes += 1

        updated_state[product_id] = {
            "title": item["title"],
            "price": item["price"],
            "series": item["series"],
            "source": item["source"],
            "url": item["url"],
            "available": item["available"],
            "quantity": item["quantity"],
        }

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    save_state(updated_state)

    print()
    print(
        f"Saved {len(updated_state)} "
        f"products to {STATE_FILE}"
    )

    print()
    print("=" * 60)
    print("RUN COMPLETE")
    print("=" * 60)

    print(
        f"Products monitored: "
        f"{len(current_products)}"
    )

    print(
        f"Restocks detected: "
        f"{restocks}"
    )

    print(
        f"Quantity changes: "
        f"{quantity_changes}"
    )

    print(
        f"New products: "
        f"{new_products}"
    )

    print("=" * 60)


if __name__ == "__main__":
    main()
