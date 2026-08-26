import json
import os
import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup


# ============================================================
# CONFIGURATION
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

STATE_FILE = "stock_state.json"

HMT_STORE_COLLECTION = "https://www.hmtwatches.store/collections/all"
HMT_IN_COLLECTION = "https://www.hmtwatches.in/collections/all"

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


# ============================================================
# PRODUCTS TO IGNORE
# ============================================================

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

def clean_text(value):
    if value is None:
        return ""

    value = str(value)

    value = value.replace("\\u003c", "<")
    value = value.replace("\\u003e", ">")
    value = value.replace("\\u0026", "&")

    return " ".join(value.split()).strip()


def product_is_excluded(title):
    title_lower = title.lower()

    for keyword in EXCLUDED_KEYWORDS:
        if keyword in title_lower:
            return True

    return False


def make_product_id(source, item):
    """
    Creates a stable ID.

    HMT Store:
        Prefer primaryProductId / SKU.

    HMT.in:
        Prefer product URL.

    Falls back to title.
    """

    primary_id = (
        item.get("primaryProductId")
        or item.get("product_id")
        or item.get("id")
        or item.get("sku")
    )

    if primary_id:
        return f"{source}:{primary_id}"

    title = clean_text(item.get("title", ""))

    return (
        f"{source}:"
        + re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    )


def extract_series(title):
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
        "Pace",
        "Plus",
        "Vihaan",
        "Elegance",
        "Chronograph",
        "Janta",
    ]

    for keyword in series_keywords:
        if re.search(
            rf"\b{re.escape(keyword)}\b",
            title,
            re.IGNORECASE,
        ):
            return keyword

    return "General"


def safe_int(value):
    try:
        if value is None:
            return None

        if isinstance(value, bool):
            return int(value)

        return int(float(str(value).replace(",", "").strip()))

    except Exception:
        return None


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_alert(item, alert_type):
    if not TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN is missing.")
        return False

    if not TELEGRAM_CHAT_ID:
        print("ERROR: TELEGRAM_CHAT_ID is missing.")
        return False

    title = item.get("title", "HMT Watch")
    price = item.get("price", "N/A")
    series = item.get("series", "General")
    quantity = item.get("quantity")

    image_url = item.get("image_url", "")
    buy_url = item.get("buy_url", HMT_STORE_COLLECTION)

    detected_time = datetime.now().strftime(
        "%d %b %Y, %I:%M %p"
    )

    if alert_type == "NEW":
        heading = "🆕 NEW HMT WATCH AVAILABLE"
    elif alert_type == "RESTOCK":
        heading = "🚨 HMT WATCH RESTOCK"
    elif alert_type == "QUANTITY":
        heading = "📦 HMT STOCK QUANTITY CHANGED"
    else:
        heading = "🚨 HMT STOCK ALERT"

    if quantity is None:
        quantity_text = "Not disclosed"
    elif quantity < 0:
        quantity_text = "Not disclosed"
    else:
        quantity_text = str(quantity)

    caption = (
        f"{heading}\n\n"
        f"⌚ <b>Product:</b> {title}\n"
        f"💰 <b>Price:</b> ₹{price}\n"
        f"🏷️ <b>Series:</b> {series}\n"
        f"📦 <b>Units in stock:</b> {quantity_text}\n"
        f"✅ <b>Status:</b> Available\n"
        f"🌐 <b>Source:</b> {item.get('source', 'HMT')}\n"
        f"🕒 <b>Detected:</b> {detected_time}\n\n"
        f'🔗 <a href="{buy_url}">Open product</a>'
    )

    api_url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    )

    # --------------------------------------------------------
    # Send photo if available
    # --------------------------------------------------------

    if image_url:
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
                timeout=20,
            )

            print(
                f"Telegram photo response: "
                f"{response.status_code}"
            )

            if response.ok:
                return True

            print(response.text)

        except Exception as exc:
            print(
                f"Telegram photo error: {exc}"
            )

    # --------------------------------------------------------
    # Fallback to text message
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
            f"Telegram message response: "
            f"{response.status_code}"
        )

        if not response.ok:
            print(response.text)

        return response.ok

    except Exception as exc:
        print(
            f"Telegram message error: {exc}"
        )

    return False


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
        ) as file:
            data = json.load(file)

        if not isinstance(data, dict):
            return {}

        return data

    except Exception as exc:
        print(
            f"Could not load state: {exc}"
        )
        return {}


def save_state(state):
    temporary_file = STATE_FILE + ".tmp"

    with open(
        temporary_file,
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            state,
            file,
            indent=2,
            ensure_ascii=False,
        )

    os.replace(
        temporary_file,
        STATE_FILE,
    )


# ============================================================
# HMT STORE
#
# IMPORTANT:
# The .store website is NOT Shopify.
# Its product data is contained inside __NEXT_DATA__.
# ============================================================

def recursively_find_products(obj, results):
    """
    Walk arbitrary JSON data and locate dictionaries that
    look like HMT Store products.
    """

    if isinstance(obj, dict):

        name = obj.get("name")

        # A product normally has name + pricing/stock fields.
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
            recursively_find_products(
                value,
                results,
            )

    elif isinstance(obj, list):

        for value in obj:
            recursively_find_products(
                value,
                results,
            )


def extract_next_data(html):
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    script = soup.find(
        "script",
        id="__NEXT_DATA__",
    )

    if not script:
        print(
            "WARNING: __NEXT_DATA__ not found."
        )
        return None

    raw = script.string or script.get_text()

    if not raw:
        print(
            "WARNING: __NEXT_DATA__ is empty."
        )
        return None

    try:
        return json.loads(raw)

    except Exception as exc:
        print(
            f"ERROR parsing __NEXT_DATA__: {exc}"
        )

        return None


def scrape_hmt_store():
    print("=" * 42)
    print("SCRAPING HMT STORE")
    print("=" * 42)

    products = {}

    try:
        response = session.get(
            HMT_STORE_COLLECTION,
            timeout=30,
        )

        print(
            "HMT Store HTTP status:",
            response.status_code,
        )

        if response.status_code != 200:
            return products

        data = extract_next_data(
            response.text
        )

        if not data:
            return products

        raw_products = []

        recursively_find_products(
            data,
            raw_products,
        )

        print(
            "HMT Store product records found:",
            len(raw_products),
        )

        seen_ids = set()

        for raw in raw_products:

            title = clean_text(
                raw.get("name")
            )

            if not title:
                continue

            # ------------------------------------------------
            # Exclusions
            # ------------------------------------------------

            if product_is_excluded(title):
                continue

            # ------------------------------------------------
            # Ignore deactivated products
            # ------------------------------------------------

            if raw.get("deactivated") is True:
                continue

            # ------------------------------------------------
            # Stable product ID
            # ------------------------------------------------

            primary_id = (
                raw.get("primaryProductId")
                or raw.get("sku")
            )

            if not primary_id:
                continue

            product_id = (
                f"store:{primary_id}"
            )

            if product_id in seen_ids:
                continue

            seen_ids.add(product_id)

            # ------------------------------------------------
            # Stock
            # ------------------------------------------------

            current_stock = safe_int(
                raw.get("currentStock")
            )

            additional_attributes = (
                raw.get("additionalAttributes")
            )

            is_oos = False

            if isinstance(
                additional_attributes,
                str,
            ):
                try:
                    additional_attributes = json.loads(
                        additional_attributes
                    )
                except Exception:
                    additional_attributes = {}

            if isinstance(
                additional_attributes,
                dict,
            ):
                is_oos = (
                    additional_attributes.get(
                        "isOOS"
                    )
                    is True
                )

            # Check variants as well.
            variants = raw.get(
                "variantsDimensions"
            )

            variant_available = False

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
                        )
                        is True
                    ):
                        variant_available = True
                        break

            # Main availability.
            available = (
                current_stock is not None
                and current_stock > 0
            ) or variant_available

            # If site explicitly says OOS and no
            # variant says available, treat as OOS.
            if is_oos and not variant_available:
                available = False

            # ------------------------------------------------
            # Price
            # ------------------------------------------------

            price = (
                raw.get("sellingPrice")
                or raw.get("mrp")
                or "N/A"
            )

            price_int = safe_int(price)

            if price_int is not None:
                price = str(price_int)

            # ------------------------------------------------
            # Image
            # ------------------------------------------------

            image_url = (
                raw.get("productImageUrl")
                or ""
            )

            if isinstance(
                image_url,
                str,
            ):
                # Handle escaped JSON / URL.
                image_match = re.search(
                    r"https://[^\"'\s\\]+",
                    image_url,
                )

                if image_match:
                    image_url = (
                        image_match.group(0)
                    )

            # ------------------------------------------------
            # Product URL
            #
            # The collection HTML doesn't expose ordinary
            # product paths, so use the collection URL as a
            # guaranteed valid fallback.
            # ------------------------------------------------

            buy_url = HMT_STORE_COLLECTION

            item = {
                "title": title,
                "price": price,
                "series": extract_series(title),
                "image_url": image_url,
                "buy_url": buy_url,
                "available": available,
                "quantity": current_stock,
                "source": "HMT Store",
                "primary_id": primary_id,
            }

            products[product_id] = item

        print(
            "HMT Store usable products:",
            len(products),
        )

    except Exception as exc:
        print(
            f"HMT Store scraping error: {exc}"
        )

    return products


# ============================================================
# HMT.IN
# ============================================================

def scrape_hmt_in():
    print("=" * 42)
    print("SCRAPING HMT.IN")
    print("=" * 42)

    products = {}

    try:
        response = session.get(
            HMT_IN_COLLECTION,
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
        # Find product URLs
        # ----------------------------------------------------

        links = []

        for anchor in soup.find_all(
            "a",
            href=True,
        ):

            href = anchor.get(
                "href",
                "",
            ).strip()

            if not href:
                continue

            href_lower = href.lower()

            # Typical Shopify/product paths.
            if (
                "/products/" in href_lower
                or "/product/" in href_lower
            ):
                if href.startswith("/"):
                    href = (
                        "https://www.hmtwatches.in"
                        + href
                    )

                elif href.startswith("//"):
                    href = "https:" + href

                if (
                    href.startswith(
                        "https://www.hmtwatches.in"
                    )
                    or href.startswith(
                        "http://www.hmtwatches.in"
                    )
                ):
                    links.append(href)

        # Deduplicate while preserving order.
        unique_links = list(
            dict.fromkeys(links)
        )

        print(
            "HMT.in product links found:",
            len(links),
        )

        print(
            "HMT.in unique product URLs:",
            len(unique_links),
        )

        # ----------------------------------------------------
        # Product pages
        # ----------------------------------------------------

        for product_url in unique_links:

            try:
                product_response = session.get(
                    product_url,
                    timeout=20,
                )

                if (
                    product_response.status_code
                    != 200
                ):
                    continue

                product_soup = BeautifulSoup(
                    product_response.text,
                    "html.parser",
                )

                # --------------------------------------------
                # Title
                # --------------------------------------------

                title = ""

                meta_title = product_soup.find(
                    "meta",
                    property="og:title",
                )

                if meta_title:
                    title = meta_title.get(
                        "content",
                        "",
                    )

                if not title:
                    h1 = product_soup.find(
                        "h1"
                    )

                    if h1:
                        title = h1.get_text(
                            " ",
                            strip=True,
                        )

                if not title and product_soup.title:
                    title = product_soup.title.get_text(
                        " ",
                        strip=True,
                    )

                title = clean_text(title)

                if not title:
                    continue

                # --------------------------------------------
                # Exclusions
                # --------------------------------------------

                if product_is_excluded(title):
                    continue

                # --------------------------------------------
                # Product JSON
                # --------------------------------------------

                product_json = None

                for script in product_soup.find_all(
                    "script",
                    type="application/ld+json",
                ):

                    raw_json = script.string

                    if not raw_json:
                        continue

                    try:
                        parsed = json.loads(
                            raw_json
                        )

                        candidates = (
                            parsed
                            if isinstance(
                                parsed,
                                list,
                            )
                            else [parsed]
                        )

                        for candidate in candidates:

                            if (
                                isinstance(
                                    candidate,
                                    dict,
                                )
                                and candidate.get(
                                    "@type"
                                )
                                == "Product"
                            ):
                                product_json = candidate
                                break

                        if product_json:
                            break

                    except Exception:
                        pass

                # --------------------------------------------
                # Price
                # --------------------------------------------

                price = "N/A"

                if product_json:
                    offers = product_json.get(
                        "offers"
                    )

                    if isinstance(
                        offers,
                        dict,
                    ):
                        price = (
                            offers.get(
                                "price"
                            )
                            or price
                        )

                if price == "N/A":

                    price_match = re.search(
                        r"(?:₹|Rs\.?|INR)\s*"
                        r"([\d,]+(?:\.\d+)?)",
                        product_soup.get_text(
                            " ",
                            strip=True,
                        ),
                        re.IGNORECASE,
                    )

                    if price_match:
                        price = (
                            price_match.group(1)
                            .replace(",", "")
                        )

                # --------------------------------------------
                # Availability
                # --------------------------------------------

                page_text = product_soup.get_text(
                    " ",
                    strip=True,
                ).lower()

                available = True

                if any(
                    phrase in page_text
                    for phrase in [
                        "sold out",
                        "out of stock",
                        "currently unavailable",
                    ]
                ):
                    available = False

                # JSON-LD availability can override text.
                if product_json:

                    offers = product_json.get(
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

                # --------------------------------------------
                # Quantity
                #
                # HMT.in may not expose exact inventory.
                # Don't invent a number.
                # --------------------------------------------

                quantity = None

                quantity_patterns = [
                    r"only\s+(\d+)\s+left",
                    r"(\d+)\s+units?\s+left",
                    r"(\d+)\s+in\s+stock",
                    r"stock[:\s]+(\d+)",
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

                # --------------------------------------------
                # Image
                # --------------------------------------------

                image_url = ""

                meta_image = product_soup.find(
                    "meta",
                    property="og:image",
                )

                if meta_image:
                    image_url = meta_image.get(
                        "content",
                        "",
                    )

                if not image_url and product_json:
                    image = product_json.get(
                        "image"
                    )

                    if isinstance(
                        image,
                        list,
                    ) and image:
                        image_url = image[0]

                    elif isinstance(
                        image,
                        str,
                    ):
                        image_url = image

                # --------------------------------------------
                # Stable ID
                # --------------------------------------------

                canonical = product_soup.find(
                    "link",
                    rel="canonical",
                )

                canonical_url = (
                    canonical.get(
                        "href"
                    )
                    if canonical
                    else product_url
                )

                product_id = (
                    "in:"
                    + re.sub(
                        r"[^a-z0-9]+",
                        "_",
                        canonical_url.lower(),
                    ).strip("_")
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

            except Exception as exc:
                print(
                    f"Error processing "
                    f"{product_url}: {exc}"
                )

        print(
            "HMT.in usable products:",
            len(products),
        )

    except Exception as exc:
        print(
            f"HMT.in scraping error: {exc}"
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

    # --------------------------------------------------------
    # Scrape both sites
    # --------------------------------------------------------

    store_products = scrape_hmt_store()
    in_products = scrape_hmt_in()

    current_catalog = {}

    current_catalog.update(
        store_products
    )

    current_catalog.update(
        in_products
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
        len(in_products),
    )

    print(
        "TOTAL:    ",
        len(current_catalog),
    )

    # --------------------------------------------------------
    # Safety check
    # --------------------------------------------------------

    if len(current_catalog) == 0:

        print()
        print(
            "WARNING: ZERO PRODUCTS FOUND."
        )

        print(
            "Previous stock state will NOT "
            "be overwritten."
        )

        return

    print()
    print(
        f"Processing {len(current_catalog)} products..."
    )

    # --------------------------------------------------------
    # Detect changes
    # --------------------------------------------------------

    updated_state = {}

    restocks = 0
    new_products = 0
    quantity_changes = 0

    for product_id, item in current_catalog.items():

        previous = previous_state.get(
            product_id
        )

        current_available = bool(
            item.get("available")
        )

        current_quantity = item.get(
            "quantity"
        )

        # ----------------------------------------------------
        # FIRST TIME SEEN
        # ----------------------------------------------------

        if previous is None:

            print(
                "BASELINE:",
                item["title"][:40],
                f"[{item['source']}]",
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
            # OUT OF STOCK -> IN STOCK
            # ------------------------------------------------

            if (
                current_available
                and not previous_available
            ):

                print(
                    "RESTOCK:",
                    item["title"],
                    f"[{item['source']}]",
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
                current_available
                and current_quantity is not None
                and previous_quantity is not None
                and current_quantity
                > previous_quantity
            ):

                print(
                    "QUANTITY INCREASE:",
                    item["title"],
                    f"{previous_quantity} -> "
                    f"{current_quantity}",
                )

                if send_telegram_alert(
                    item,
                    "QUANTITY",
                ):
                    quantity_changes += 1

        # ----------------------------------------------------
        # Save current state
        # ----------------------------------------------------

        updated_state[product_id] = {
            "title": item.get("title"),
            "price": item.get("price"),
            "available": current_available,
            "quantity": current_quantity,
            "source": item.get("source"),
            "image_url": item.get("image_url"),
            "buy_url": item.get("buy_url"),
            "series": item.get("series"),
        }

    # --------------------------------------------------------
    # Save state
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
