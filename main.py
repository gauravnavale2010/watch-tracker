import json
import os
import re
from datetime import datetime
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
HMT_IN_URL = "https://www.hmtwatches.in/all_product"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}

session = requests.Session()
session.headers.update(HEADERS)


# ============================================================
# EXCLUSIONS
# ============================================================

EXCLUDED_KEYWORDS = [
    "galaxy",
    "inox",
]


# ============================================================
# HELPERS
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    return " ".join(str(value).split()).strip()


def excluded(title):
    title_lower = title.lower()

    return any(
        word in title_lower
        for word in EXCLUDED_KEYWORDS
    )


def safe_int(value):
    try:
        if value is None:
            return None

        return int(
            float(
                str(value)
                .replace(",", "")
                .strip()
            )
        )

    except Exception:
        return None


def series_from_title(title):
    keywords = [
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
        "Pace",
        "Plus",
        "Vihaan",
        "Elegance",
        "Chronograph",
        "Janta",
        "Ravi",
    ]

    for keyword in keywords:
        if re.search(
            rf"\b{re.escape(keyword)}\b",
            title,
            re.IGNORECASE,
        ):
            return keyword

    return "General"


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

        if isinstance(data, dict):
            return data

    except Exception as e:
        print(
            "Could not load state:",
            e,
        )

    return {}


def save_state(state):

    temp_file = STATE_FILE + ".tmp"

    with open(
        temp_file,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            state,
            f,
            indent=2,
            ensure_ascii=False,
        )

    os.replace(
        temp_file,
        STATE_FILE,
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(item, alert_type):

    if not TELEGRAM_BOT_TOKEN:
        print(
            "ERROR: TELEGRAM_BOT_TOKEN missing"
        )
        return False

    if not TELEGRAM_CHAT_ID:
        print(
            "ERROR: TELEGRAM_CHAT_ID missing"
        )
        return False

    title = item.get(
        "title",
        "HMT Watch",
    )

    price = item.get(
        "price",
        "N/A",
    )

    quantity = item.get(
        "quantity"
    )

    source = item.get(
        "source",
        "HMT",
    )

    buy_url = item.get(
        "buy_url",
        "",
    )

    image_url = item.get(
        "image_url",
        "",
    )

    if alert_type == "RESTOCK":
        heading = "🚨 HMT WATCH RESTOCK"

    elif alert_type == "QUANTITY":
        heading = "📦 HMT STOCK INCREASE"

    elif alert_type == "NEW":
        heading = "🆕 NEW HMT WATCH"

    else:
        heading = "🚨 HMT STOCK ALERT"

    if quantity is None:
        quantity_text = "Not disclosed"
    else:
        quantity_text = str(quantity)

    detected = datetime.now().strftime(
        "%d %b %Y, %I:%M %p"
    )

    caption = (
        f"{heading}\n\n"
        f"⌚ <b>Product:</b> {title}\n"
        f"💰 <b>Price:</b> ₹{price}\n"
        f"🏷️ <b>Series:</b> "
        f"{series_from_title(title)}\n"
        f"📦 <b>Units in stock:</b> "
        f"{quantity_text}\n"
        f"🌐 <b>Source:</b> {source}\n"
        f"🕒 <b>Detected:</b> {detected}\n"
        f"🔗 <a href=\"{buy_url}\">Open product</a>"
    )

    # --------------------------------------------------------
    # PHOTO
    # --------------------------------------------------------

    if image_url:

        photo_url = (
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

            response = session.post(
                photo_url,
                data=payload,
                timeout=20,
            )

            print(
                "Telegram photo response:",
                response.status_code,
            )

            if response.ok:
                return True

            print(
                response.text
            )

        except Exception as e:

            print(
                "Telegram photo error:",
                e,
            )

    # --------------------------------------------------------
    # TEXT FALLBACK
    # --------------------------------------------------------

    message_url = (
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
            message_url,
            data=payload,
            timeout=20,
        )

        print(
            "Telegram message response:",
            response.status_code,
        )

        if response.ok:
            return True

        print(
            response.text
        )

    except Exception as e:

        print(
            "Telegram message error:",
            e,
        )

    return False


# ============================================================
# HMT STORE
#
# .store uses Next.js __NEXT_DATA__
# ============================================================

def find_store_products(obj, results):

    if isinstance(obj, dict):

        name = obj.get("name")

        if (
            isinstance(name, str)
            and name.strip()
            and (
                "currentStock" in obj
                or "primaryProductId" in obj
                or "sellingPrice" in obj
            )
        ):
            results.append(obj)

        for value in obj.values():

            find_store_products(
                value,
                results,
            )

    elif isinstance(obj, list):

        for value in obj:

            find_store_products(
                value,
                results,
            )


def scrape_store():

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
            "HMT Store HTTP status:",
            response.status_code,
        )

        if response.status_code != 200:
            return products

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        next_script = soup.find(
            "script",
            id="__NEXT_DATA__",
        )

        if not next_script:

            print(
                "HMT Store __NEXT_DATA__ not found"
            )

            return products

        raw = (
            next_script.string
            or next_script.get_text()
        )

        data = json.loads(raw)

        raw_products = []

        find_store_products(
            data,
            raw_products,
        )

        print(
            "HMT Store product records found:",
            len(raw_products),
        )

        seen = set()

        for product in raw_products:

            title = clean_text(
                product.get("name")
            )

            if not title:
                continue

            if excluded(title):
                continue

            if product.get(
                "deactivated"
            ) is True:
                continue

            product_code = (
                product.get(
                    "primaryProductId"
                )
                or product.get("sku")
                or product.get("id")
            )

            if not product_code:
                continue

            product_id = (
                "store:"
                + str(product_code)
            )

            if product_id in seen:
                continue

            seen.add(product_id)

            quantity = safe_int(
                product.get(
                    "currentStock"
                )
            )

            available = (
                quantity is not None
                and quantity > 0
            )

            # Variant fallback
            variants = product.get(
                "variantsDimensions"
            )

            if isinstance(
                variants,
                list,
            ):

                for variant in variants:

                    if (
                        isinstance(
                            variant,
                            dict,
                        )
                        and variant.get(
                            "inStock"
                        ) is True
                    ):
                        available = True
                        break

            price = (
                product.get(
                    "sellingPrice"
                )
                or product.get("mrp")
                or "N/A"
            )

            price_int = safe_int(price)

            if price_int is not None:
                price = str(price_int)

            image_url = (
                product.get(
                    "productImageUrl"
                )
                or ""
            )

            products[product_id] = {
                "title": title,
                "price": price,
                "available": available,
                "quantity": quantity,
                "source": "HMT Store",
                "series": series_from_title(
                    title
                ),
                "image_url": image_url,
                "buy_url": HMT_STORE_URL,
            }

        print(
            "HMT Store usable products:",
            len(products),
        )

    except Exception as e:

        print(
            "HMT Store error:",
            e,
        )

    return products


# ============================================================
# HMT.IN
#
# Correct catalog:
# https://www.hmtwatches.in/all_product
#
# Product pages:
# /product_details?id=...
# ============================================================

def scrape_hmt_in():

    print("=" * 42)
    print("SCRAPING HMT.IN")
    print("=" * 42)

    products = {}

    try:

        response = session.get(
            HMT_IN_URL,
            timeout=30,
        )

        print(
            "HMT.in HTTP status:",
            response.status_code,
        )

        if response.status_code != 200:
            return products

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        # ----------------------------------------------------
        # Find actual product-detail links
        # ----------------------------------------------------

        product_links = []

        for a in soup.find_all(
            "a",
            href=True,
        ):

            href = a.get(
                "href",
                "",
            ).strip()

            if not href:
                continue

            href_lower = href.lower()

            if (
                "product_details" in href_lower
                or "product_detail" in href_lower
            ):

                full_url = urljoin(
                    HMT_IN_URL,
                    href,
                )

                product_links.append(
                    full_url
                )

        # ----------------------------------------------------
        # Some pages may encode links differently.
        # Search raw HTML as fallback.
        # ----------------------------------------------------

        raw_links = re.findall(
            r'href=["\']([^"\']+)["\']',
            response.text,
            re.IGNORECASE,
        )

        for href in raw_links:

            if (
                "product_details"
                in href.lower()
            ):

                full_url = urljoin(
                    HMT_IN_URL,
                    href,
                )

                product_links.append(
                    full_url
                )

        product_links = list(
            dict.fromkeys(
                product_links
            )
        )

        print(
            "HMT.in product links found:",
            len(product_links),
        )

        # ----------------------------------------------------
        # If the catalog page exposes product cards but
        # product links are hidden, parse the cards directly.
        # ----------------------------------------------------

        card_products = parse_hmt_in_cards(
            soup
        )

        # ----------------------------------------------------
        # First process direct product links.
        # ----------------------------------------------------

        for product_url in product_links:

            item = scrape_hmt_in_product(
                product_url
            )

            if not item:
                continue

            title = item["title"]

            if excluded(title):
                continue

            product_id = item["product_id"]

            products[
                product_id
            ] = item

        # ----------------------------------------------------
        # Add catalog cards that weren't picked up through
        # detail links.
        # ----------------------------------------------------

        for product_id, item in card_products.items():

            if product_id not in products:

                products[
                    product_id
                ] = item

        print(
            "HMT.in usable products:",
            len(products),
        )

    except Exception as e:

        print(
            "HMT.in error:",
            e,
        )

    return products


# ============================================================
# HMT.IN PRODUCT PAGE
# ============================================================

def scrape_hmt_in_product(url):

    try:

        response = session.get(
            url,
            timeout=20,
        )

        if response.status_code != 200:
            return None

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        # ----------------------------------------------------
        # TITLE
        # ----------------------------------------------------

        title = ""

        h1 = soup.find("h1")

        if h1:
            title = clean_text(
                h1.get_text(
                    " ",
                    strip=True,
                )
            )

        if not title:

            og_title = soup.find(
                "meta",
                property="og:title",
            )

            if og_title:
                title = clean_text(
                    og_title.get(
                        "content",
                        "",
                    )
                )

        if not title:
            return None

        if excluded(title):
            return None

        # ----------------------------------------------------
        # PRICE
        # ----------------------------------------------------

        price = "N/A"

        price_patterns = [
            r"₹\s*([\d,]+)",
            r"Rs\.?\s*([\d,]+)",
            r"RS\.?\s*([\d,]+)",
            r"INR\s*([\d,]+)",
        ]

        page_text = soup.get_text(
            " ",
            strip=True,
        )

        for pattern in price_patterns:

            match = re.search(
                pattern,
                page_text,
                re.IGNORECASE,
            )

            if match:

                price = (
                    match.group(1)
                    .replace(",", "")
                )

                break

        # ----------------------------------------------------
        # AVAILABILITY
        # ----------------------------------------------------

        lower_text = page_text.lower()

        available = True

        out_of_stock_phrases = [
            "out of stock",
            "sold out",
            "currently unavailable",
        ]

        if any(
            phrase in lower_text
            for phrase in out_of_stock_phrases
        ):
            available = False

        # ----------------------------------------------------
        # JSON-LD AVAILABILITY
        # ----------------------------------------------------

        quantity = None

        for script in soup.find_all(
            "script",
            type="application/ld+json",
        ):

            raw = (
                script.string
                or script.get_text()
            )

            if not raw:
                continue

            try:

                data = json.loads(
                    raw
                )

            except Exception:
                continue

            candidates = (
                data
                if isinstance(
                    data,
                    list,
                )
                else [data]
            )

            for obj in candidates:

                if not isinstance(
                    obj,
                    dict,
                ):
                    continue

                if obj.get(
                    "@type"
                ) != "Product":
                    continue

                offers = obj.get(
                    "offers"
                )

                if isinstance(
                    offers,
                    dict,
                ):

                    availability = str(
                        offers.get(
                            "availability",
                            "",
                        )
                    ).lower()

                    if (
                        "outofstock"
                        in availability
                    ):
                        available = False

                    elif (
                        "instock"
                        in availability
                    ):
                        available = True

                break

        # ----------------------------------------------------
        # EXACT QUANTITY
        #
        # Only report it if the page explicitly exposes it.
        # ----------------------------------------------------

        quantity_patterns = [
            r"only\s+(\d+)\s+left",
            r"(\d+)\s+units?\s+left",
            r"(\d+)\s+in\s+stock",
            r"stock[:\s]+(\d+)",
            r"quantity[:\s]+(\d+)",
        ]

        for pattern in quantity_patterns:

            match = re.search(
                pattern,
                page_text,
                re.IGNORECASE,
            )

            if match:

                quantity = safe_int(
                    match.group(1)
                )

                break

        # ----------------------------------------------------
        # IMAGE
        # ----------------------------------------------------

        image_url = ""

        og_image = soup.find(
            "meta",
            property="og:image",
        )

        if og_image:

            image_url = og_image.get(
                "content",
                "",
            )

        # ----------------------------------------------------
        # STABLE ID
        # ----------------------------------------------------

        product_id = (
            "in:"
            + re.sub(
                r"[^a-z0-9]+",
                "_",
                url.lower(),
            ).strip("_")
        )

        return {
            "product_id": product_id,
            "title": title,
            "price": price,
            "available": available,
            "quantity": quantity,
            "source": "HMT.in",
            "series": series_from_title(
                title
            ),
            "image_url": image_url,
            "buy_url": url,
        }

    except Exception as e:

        print(
            "Product page error:",
            url,
            e,
        )

        return None


# ============================================================
# HMT.IN CARD FALLBACK
# ============================================================

def parse_hmt_in_cards(soup):

    products = {}

    # Search all anchors that look like product-detail
    # links and extract their surrounding card information.

    for a in soup.find_all(
        "a",
        href=True,
    ):

        href = a.get(
            "href",
            "",
        )

        if (
            "product_details"
            not in href.lower()
        ):
            continue

        url = urljoin(
            HMT_IN_URL,
            href,
        )

        # Find nearest useful parent.
        parent = a

        for _ in range(5):

            if parent.parent:
                parent = parent.parent

            text = clean_text(
                parent.get_text(
                    " ",
                    strip=True,
                )
            )

            if len(text) > 20:
                break

        title = clean_text(
            a.get_text(
                " ",
                strip=True,
            )
        )

        if not title:

            title_element = parent.find(
                [
                    "h2",
                    "h3",
                    "h4",
                    "strong",
                    "span",
                ]
            )

            if title_element:

                title = clean_text(
                    title_element.get_text(
                        " ",
                        strip=True,
                    )
                )

        if not title:
            continue

        if excluded(title):
            continue

        text_lower = (
            parent.get_text(
                " ",
                strip=True,
            ).lower()
        )

        available = (
            "out of stock"
            not in text_lower
            and "sold out"
            not in text_lower
        )

        price = "N/A"

        match = re.search(
            r"(?:₹|rs\.?|inr)\s*([\d,]+)",
            text_lower,
            re.IGNORECASE,
        )

        if match:

            price = (
                match.group(1)
                .replace(",", "")
            )

        image_url = ""

        img = parent.find(
            "img"
        )

        if img:

            image_url = (
                img.get("src")
                or img.get(
                    "data-src"
                )
                or ""
            )

            image_url = urljoin(
                url,
                image_url,
            )

        product_id = (
            "in:"
            + re.sub(
                r"[^a-z0-9]+",
                "_",
                url.lower(),
            ).strip("_")
        )

        products[
            product_id
        ] = {
            "product_id": product_id,
            "title": title,
            "price": price,
            "available": available,
            "quantity": None,
            "source": "HMT.in",
            "series": series_from_title(
                title
            ),
            "image_url": image_url,
            "buy_url": url,
        }

    return products


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)
    print("HMT WATCH RESTOCK TRACKER")
    print("=" * 60)

    previous_state = load_state()

    # --------------------------------------------------------
    # SCRAPE BOTH OFFICIAL SITES
    # --------------------------------------------------------

    store_products = scrape_store()

    hmt_in_products = scrape_hmt_in()

    current_catalog = {}

    current_catalog.update(
        store_products
    )

    current_catalog.update(
        hmt_in_products
    )

    print()
    print("=" * 42)
    print("SCRAPE SUMMARY")
    print("=" * 42)

    print(
        "HMT Store:",
        len(store_products),
    )

    print(
        "HMT.in:   ",
        len(hmt_in_products),
    )

    print(
        "TOTAL:    ",
        len(current_catalog),
    )

    # --------------------------------------------------------
    # SAFETY CHECK
    # --------------------------------------------------------

    if len(current_catalog) == 0:

        print()
        print(
            "WARNING: ZERO PRODUCTS FOUND."
        )

        print(
            "Stock state will NOT be overwritten."
        )

        return

    print()
    print(
        f"Processing {len(current_catalog)} products..."
    )

    updated_state = {}

    restocks = 0
    quantity_changes = 0
    new_products = 0

    # --------------------------------------------------------
    # PROCESS PRODUCTS
    # --------------------------------------------------------

    for product_id, item in current_catalog.items():

        title = item.get(
            "title",
            "Unknown",
        )

        current_available = bool(
            item.get(
                "available",
                False,
            )
        )

        current_quantity = item.get(
            "quantity"
        )

        previous = previous_state.get(
            product_id
        )

        # ----------------------------------------------------
        # NEW PRODUCT
        # ----------------------------------------------------

        if previous is None:

            print(
                "BASELINE:",
                title[:40],
                f"[{item.get('source')}]",
            )

        else:

            previous_available = bool(
                previous.get(
                    "available",
                    False,
                )
            )

            previous_quantity = previous.get(
                "quantity"
            )

            # ------------------------------------------------
            # RESTOCK
            # ------------------------------------------------

            if (
                current_available
                and not previous_available
            ):

                print(
                    "RESTOCK:",
                    title,
                    f"[{item.get('source')}]",
                )

                if send_telegram(
                    item,
                    "RESTOCK",
                ):
                    restocks += 1

            # ------------------------------------------------
            # QUANTITY INCREASE
            # ------------------------------------------------

            elif (
                current_available
                and current_quantity is not None
                and previous_quantity is not None
                and current_quantity
                > previous_quantity
            ):

                print(
                    "QUANTITY INCREASE:",
                    title,
                    f"{previous_quantity} -> "
                    f"{current_quantity}",
                )

                if send_telegram(
                    item,
                    "QUANTITY",
                ):
                    quantity_changes += 1

        # ----------------------------------------------------
        # SAVE CURRENT STATE
        # ----------------------------------------------------

        updated_state[
            product_id
        ] = {
            "title": title,
            "price": item.get(
                "price"
            ),
            "available": current_available,
            "quantity": current_quantity,
            "source": item.get(
                "source"
            ),
            "image_url": item.get(
                "image_url"
            ),
            "buy_url": item.get(
                "buy_url"
            ),
            "series": item.get(
                "series"
            ),
        }

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    save_state(
        updated_state
    )

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
        "Products monitored:",
        len(updated_state),
    )

    print(
        "Restocks detected:",
        restocks,
    )

    print(
        "Quantity changes:",
        quantity_changes,
    )

    print(
        "New products:",
        new_products,
    )

    print("=" * 60)


if __name__ == "__main__":
    main()
