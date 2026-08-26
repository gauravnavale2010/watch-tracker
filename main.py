import os
import json
import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


# ============================================================
# CONFIG
# ============================================================

HMT_STORE_URL = "https://www.hmtwatches.store/collections/all"

HMT_IN_URLS = [
    "https://www.hmtwatches.in/all_product",
    "https://www.hmtwatches.in/watches",
    "https://www.hmtwatches.in/mens",
    "https://www.hmtwatches.in/womens",
]

STATE_FILE = "stock_state.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/139.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Cache-Control": "no-cache",
}

TIMEOUT = 30


# ============================================================
# HELPERS
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    value = str(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def absolute_url(base, url):
    if not url:
        return ""

    url = url.strip()

    if url.startswith("//"):
        return "https:" + url

    return urljoin(base, url)


def normalize_url(url):
    if not url:
        return ""

    parsed = urlparse(url)

    # Remove fragments
    clean = parsed._replace(fragment="")

    return clean.geturl()


def is_real_product_name(name):
    if not name:
        return False

    name = clean_text(name)

    bad = {
        "product detail",
        "product",
        "details",
        "view details",
        "view product",
        "read more",
        "buy now",
        "add to cart",
        "shop now",
        "top picks",
    }

    if name.lower() in bad:
        return False

    if len(name) < 4:
        return False

    return True


def parse_price(text):
    if not text:
        return None

    text = clean_text(text)

    patterns = [
        r"₹\s*([\d,]+(?:\.\d+)?)",
        r"Rs\.?\s*([\d,]+(?:\.\d+)?)",
        r"INR\s*([\d,]+(?:\.\d+)?)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.I)

        if match:
            try:
                return float(match.group(1).replace(",", ""))
            except Exception:
                pass

    return None


def parse_quantity(text):
    """
    Attempts to detect an explicit quantity such as:
    - Only 3 left
    - 3 units left
    - Available quantity: 3
    - Quantity: 3

    If the website doesn't expose the actual inventory count,
    returns None rather than inventing a number.
    """

    if not text:
        return None

    text = clean_text(text)

    patterns = [
        r"(?:only|just)\s+(\d+)\s+(?:left|remaining)",
        r"(\d+)\s+(?:units?|pieces?)\s+(?:left|remaining)",
        r"(?:available\s+quantity|quantity\s+available)\s*[:\-]?\s*(\d+)",
        r"(?:stock|inventory)\s*[:\-]?\s*(\d+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.I)

        if match:
            try:
                return int(match.group(1))
            except Exception:
                pass

    return None


def detect_stock(text):
    """
    Returns:
        True  = in stock
        False = out of stock
        None  = unknown
    """

    if not text:
        return None

    text = clean_text(text).lower()

    out_patterns = [
        "out of stock",
        "sold out",
        "currently unavailable",
        "unavailable",
        "not available",
    ]

    in_patterns = [
        "in stock",
        "available",
        "add to cart",
        "buy now",
    ]

    for phrase in out_patterns:
        if phrase in text:
            return False

    for phrase in in_patterns:
        if phrase in text:
            return True

    return None


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials not configured.")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": False,
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=20,
        )

        if response.ok:
            print("Telegram notification sent.")
            return True

        print(
            "Telegram error:",
            response.status_code,
            response.text[:500],
        )

    except Exception as e:
        print("Telegram exception:", e)

    return False


# ============================================================
# HMT STORE
# ============================================================

def recursive_product_objects(obj, found):
    """
    Generic recursive extraction from Next.js / React JSON data.
    This is intentionally flexible because the HMT Store is not
    Shopify and its frontend structure can change.
    """

    if isinstance(obj, dict):

        keys_lower = {
            str(k).lower(): k
            for k in obj.keys()
        }

        name_key = None

        for candidate in [
            "name",
            "productname",
            "product_name",
            "title",
            "producttitle",
            "product_title",
        ]:
            if candidate in keys_lower:
                name_key = keys_lower[candidate]
                break

        if name_key:
            name = clean_text(obj.get(name_key))

            if is_real_product_name(name):

                url = ""

                for candidate in [
                    "url",
                    "producturl",
                    "product_url",
                    "link",
                    "productlink",
                    "product_link",
                    "slug",
                ]:
                    if candidate in keys_lower:
                        value = obj.get(keys_lower[candidate])

                        if isinstance(value, str):
                            url = value
                            break

                price = None

                for candidate in [
                    "price",
                    "saleprice",
                    "sale_price",
                    "sellingprice",
                    "selling_price",
                    "amount",
                ]:
                    if candidate in keys_lower:
                        value = obj.get(keys_lower[candidate])

                        if isinstance(value, (int, float)):
                            price = float(value)
                            break

                        if isinstance(value, str):
                            price = parse_price(value)

                            if price is not None:
                                break

                stock_text_parts = []

                for key, value in obj.items():

                    key_l = str(key).lower()

                    if any(
                        word in key_l
                        for word in [
                            "stock",
                            "available",
                            "quantity",
                            "inventory",
                        ]
                    ):
                        stock_text_parts.append(str(value))

                stock_text = " ".join(stock_text_parts)

                stock = detect_stock(stock_text)
                quantity = parse_quantity(stock_text)

                found.append({
                    "name": name,
                    "url": url,
                    "price": price,
                    "stock": stock,
                    "quantity": quantity,
                })

        for value in obj.values():
            recursive_product_objects(value, found)

    elif isinstance(obj, list):
        for item in obj:
            recursive_product_objects(item, found)


def extract_json_scripts(soup):
    results = []

    for script in soup.find_all("script"):

        script_type = script.get("type", "")

        text = script.string or script.get_text()

        if not text:
            continue

        text = text.strip()

        if (
            "json" not in script_type.lower()
            and not text.startswith("{")
            and not text.startswith("[")
        ):
            continue

        try:
            data = json.loads(text)
            results.append(data)
        except Exception:
            continue

    return results


def scrape_hmt_store():
    print("=" * 42)
    print("SCRAPING HMT STORE")
    print("=" * 42)

    try:
        response = requests.get(
            HMT_STORE_URL,
            headers=HEADERS,
            timeout=TIMEOUT,
        )

        print("HMT Store HTTP status:", response.status_code)

        if response.status_code != 200:
            return []

        html = response.text

        soup = BeautifulSoup(html, "html.parser")

        products = []

        # ----------------------------------------------------
        # 1. Try JSON / Next.js data
        # ----------------------------------------------------

        json_data = extract_json_scripts(soup)

        json_products = []

        for data in json_data:
            recursive_product_objects(
                data,
                json_products,
            )

        products.extend(json_products)

        # ----------------------------------------------------
        # 2. Try product links
        # ----------------------------------------------------

        candidate_links = []

        for a in soup.find_all("a", href=True):

            href = absolute_url(
                HMT_STORE_URL,
                a.get("href"),
            )

            text = clean_text(a.get_text(" ", strip=True))

            if not href:
                continue

            href_lower = href.lower()

            looks_product = any(
                token in href_lower
                for token in [
                    "/product/",
                    "/products/",
                    "/item/",
                    "/product-details/",
                    "/product_details/",
                ]
            )

            if not looks_product:
                continue

            if not is_real_product_name(text):
                continue

            candidate_links.append(
                (text, href)
            )

        print(
            "HMT Store candidate links:",
            len(candidate_links),
        )

        for name, url in candidate_links:

            products.append({
                "name": name,
                "url": normalize_url(url),
                "price": None,
                "stock": None,
                "quantity": None,
            })

        # ----------------------------------------------------
        # 3. Generic card extraction
        # ----------------------------------------------------

        for element in soup.find_all(
            ["article", "li", "div"]
        ):

            text = clean_text(
                element.get_text(" ", strip=True)
            )

            if not text:
                continue

            # Avoid giant page-level containers
            if len(text) > 1200:
                continue

            price = parse_price(text)

            if price is None:
                continue

            links = element.find_all(
                "a",
                href=True,
            )

            for link in links:

                name = clean_text(
                    link.get_text(" ", strip=True)
                )

                if not is_real_product_name(name):
                    continue

                href = absolute_url(
                    HMT_STORE_URL,
                    link.get("href"),
                )

                if not href:
                    continue

                products.append({
                    "name": name,
                    "url": normalize_url(href),
                    "price": price,
                    "stock": detect_stock(text),
                    "quantity": parse_quantity(text),
                })

        # ----------------------------------------------------
        # DEDUPLICATE
        # ----------------------------------------------------

        unique = {}

        for product in products:

            name = clean_text(
                product.get("name")
            )

            url = normalize_url(
                product.get("url")
            )

            if not is_real_product_name(name):
                continue

            # URL is preferred as unique ID.
            # If URL isn't available, use name + price.
            if url:
                key = url
            else:
                key = (
                    name.lower(),
                    product.get("price"),
                )

            if key not in unique:
                product["name"] = name
                product["url"] = url
                unique[key] = product

        products = list(unique.values())

        print(
            "HMT Store usable products:",
            len(products),
        )

        return products

    except Exception as e:
        print("HMT Store scrape error:", e)
        return []


# ============================================================
# HMT.IN
# ============================================================

def extract_hmt_in_listing_products(
    html,
    source_url,
):
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    products = []

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # The listing page itself contains:
    #
    # HMT Pace UGSL 101 ..
    # RS. 2200
    # Out Of Stock
    #
    # We therefore use the anchor text from the listing,
    # rather than visiting the detail page and getting
    # "Product Detail".
    # --------------------------------------------------------

    for a in soup.find_all("a", href=True):

        href = a.get("href")

        if not href:
            continue

        full_url = absolute_url(
            source_url,
            href,
        )

        href_lower = full_url.lower()

        if "product_details" not in href_lower:
            continue

        name = clean_text(
            a.get_text(" ", strip=True)
        )

        # Sometimes the anchor itself contains no text.
        # Look at the immediate parent/card.
        container = a

        for _ in range(4):

            if container.parent:
                container = container.parent

            text = clean_text(
                container.get_text(
                    " ",
                    strip=True,
                )
            )

            if (
                is_real_product_name(name)
                and len(text) < 1000
            ):
                break

            # Find likely product title elements.
            for child in container.find_all(
                ["h1", "h2", "h3", "h4", "strong", "span"],
                limit=30,
            ):

                candidate = clean_text(
                    child.get_text(
                        " ",
                        strip=True,
                    )
                )

                if is_real_product_name(candidate):
                    name = candidate

                    if (
                        "product detail"
                        not in candidate.lower()
                    ):
                        break

            if is_real_product_name(name):
                break

        if not is_real_product_name(name):
            continue

        card_text = clean_text(
            container.get_text(
                " ",
                strip=True,
            )
        )

        price = parse_price(card_text)
        stock = detect_stock(card_text)
        quantity = parse_quantity(card_text)

        products.append({
            "name": name,
            "url": normalize_url(full_url),
            "price": price,
            "stock": stock,
            "quantity": quantity,
        })

    return products


def scrape_hmt_in():
    print("=" * 42)
    print("SCRAPING HMT.IN")
    print("=" * 42)

    all_products = []

    seen_urls = set()

    for url in HMT_IN_URLS:

        try:
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=TIMEOUT,
            )

            print(
                f"HMT.in HTTP status ({url}):",
                response.status_code,
            )

            if response.status_code != 200:
                continue

            products = extract_hmt_in_listing_products(
                response.text,
                url,
            )

            for product in products:

                product_url = product.get(
                    "url",
                    "",
                )

                if not product_url:
                    continue

                if product_url in seen_urls:
                    continue

                seen_urls.add(product_url)

                all_products.append(product)

            # Small delay between pages
            time.sleep(0.5)

        except Exception as e:
            print(
                f"HMT.in error ({url}):",
                e,
            )

    print(
        "HMT.in product links found:",
        len(seen_urls),
    )

    print(
        "HMT.in usable products:",
        len(all_products),
    )

    return all_products


# ============================================================
# STATE
# ============================================================

def load_state():
    if not os.path.exists(STATE_FILE):
        return None

    try:
        with open(
            STATE_FILE,
            "r",
            encoding="utf-8",
        ) as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return None

        return data

    except Exception as e:
        print("Could not load state:", e)
        return None


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


# ============================================================
# PRODUCT ID
# ============================================================

def product_key(product):
    """
    URL is the most reliable identifier.

    If URL isn't available, fall back to:
        source + name + price
    """

    url = normalize_url(
        product.get("url")
    )

    if url:
        return url

    return "|".join([
        product.get("source", ""),
        clean_text(product.get("name", "")).lower(),
        str(product.get("price", "")),
    ])


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

def format_product(product):
    name = product.get(
        "name",
        "Unknown HMT watch",
    )

    source = product.get(
        "source",
        "",
    )

    url = product.get(
        "url",
        "",
    )

    price = product.get(
        "price"
    )

    stock = product.get(
        "stock"
    )

    quantity = product.get(
        "quantity"
    )

    lines = [
        f"⌚ HMT STOCK ALERT",
        "",
        f"{name}",
        f"Source: {source}",
    ]

    if price is not None:
        if float(price).is_integer():
            price_text = f"₹{int(price):,}"
        else:
            price_text = f"₹{price:,.2f}"

        lines.append(
            f"Price: {price_text}"
        )

    if stock is True:
        lines.append(
            "Stock: IN STOCK"
        )

    elif stock is False:
        lines.append(
            "Stock: OUT OF STOCK"
        )

    else:
        lines.append(
            "Stock: STATUS UNKNOWN"
        )

    if quantity is not None:
        lines.append(
            f"Units available: {quantity}"
        )

    if url:
        lines.extend([
            "",
            url,
        ])

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)
    print("HMT WATCH RESTOCK TRACKER")
    print("=" * 60)

    # --------------------------------------------------------
    # SCRAPE
    # --------------------------------------------------------

    store_products = scrape_hmt_store()

    for product in store_products:
        product["source"] = "HMT Store"

    hmt_products = scrape_hmt_in()

    for product in hmt_products:
        product["source"] = "HMT.in"

    products = (
        store_products +
        hmt_products
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
        len(hmt_products),
    )

    print(
        "TOTAL:    ",
        len(products),
    )

    # --------------------------------------------------------
    # ZERO-PRODUCT SAFETY
    # --------------------------------------------------------

    if not products:

        print()
        print(
            "WARNING: ZERO PRODUCTS FOUND."
        )

        print(
            "Existing stock_state.json "
            "will NOT be overwritten."
        )

        return

    # --------------------------------------------------------
    # BUILD CURRENT STATE
    # --------------------------------------------------------

    current_state = {}

    for product in products:

        key = product_key(product)

        if not key:
            continue

        current_state[key] = product

    print()
    print(
        "Processing",
        len(current_state),
        "unique products..."
    )

    # --------------------------------------------------------
    # LOAD PREVIOUS STATE
    # --------------------------------------------------------

    previous_state = load_state()

    if previous_state is None:
        print()
        print(
            "No previous state found."
        )
        print(
            "Creating initial baseline."
        )

        save_state(current_state)

        print()
        print(
            f"Saved {len(current_state)} "
            "products to stock_state.json"
        )

        print()
        print("=" * 60)
        print("INITIAL BASELINE COMPLETE")
        print("=" * 60)

        print(
            "Products monitored:",
            len(current_state),
        )

        print(
            "Restocks detected: 0"
        )

        print(
            "Quantity changes: 0"
        )

        print(
            "New products: 0"
        )

        print(
            "No Telegram alerts sent "
            "during initial baseline."
        )

        print("=" * 60)

        return

    # --------------------------------------------------------
    # DETECT CHANGES
    # --------------------------------------------------------

    restocks = []
    quantity_changes = []
    new_products = []

    for key, current in current_state.items():

        previous = previous_state.get(key)

        name = current.get(
            "name",
            "Unknown",
        )

        source = current.get(
            "source",
            "",
        )

        if previous is None:

            print(
                f"NEW PRODUCT: {name} "
                f"[{source}]"
            )

            new_products.append(
                current
            )

            # IMPORTANT:
            # Do NOT alert for a new product.
            #
            # This prevents the first successful scrape
            # from generating 40+ Telegram messages.

            continue

        old_stock = previous.get(
            "stock"
        )

        new_stock = current.get(
            "stock"
        )

        old_quantity = previous.get(
            "quantity"
        )

        new_quantity = current.get(
            "quantity"
        )

        # ----------------------------------------------------
        # RESTOCK
        # ----------------------------------------------------

        if (
            old_stock is False
            and new_stock is True
        ):

            print(
                f"RESTOCK: {name} "
                f"[{source}]"
            )

            restocks.append(
                current
            )

        # ----------------------------------------------------
        # QUANTITY CHANGE
        # ----------------------------------------------------

        elif (
            old_quantity is not None
            and new_quantity is not None
            and old_quantity != new_quantity
        ):

            print(
                f"QUANTITY CHANGE: {name} "
                f"{old_quantity} -> {new_quantity}"
            )

            quantity_changes.append(
                (
                    previous,
                    current,
                )
            )

    # --------------------------------------------------------
    # SAVE STATE
    # --------------------------------------------------------

    save_state(current_state)

    print()
    print(
        f"Saved {len(current_state)} "
        "products to stock_state.json"
    )

    # --------------------------------------------------------
    # SEND RESTOCK ALERTS
    # --------------------------------------------------------

    for product in restocks:

        send_telegram(
            format_product(product)
        )

        time.sleep(1)

    # --------------------------------------------------------
    # SEND QUANTITY ALERTS
    # --------------------------------------------------------

    for previous, current in quantity_changes:

        message = format_product(current)

        old_quantity = previous.get(
            "quantity"
        )

        new_quantity = current.get(
            "quantity"
        )

        message += (
            "\n\n"
            f"Quantity changed: "
            f"{old_quantity} → {new_quantity}"
        )

        send_telegram(message)

        time.sleep(1)

    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------

    print()
    print("=" * 60)
    print("RUN COMPLETE")
    print("=" * 60)

    print(
        "Products monitored:",
        len(current_state),
    )

    print(
        "Restocks detected:",
        len(restocks),
    )

    print(
        "Quantity changes:",
        len(quantity_changes),
    )

    print(
        "New products:",
        len(new_products),
    )

    print("=" * 60)


if __name__ == "__main__":
    main()
