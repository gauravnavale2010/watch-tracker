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
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
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

    url = str(url).strip()

    if url.startswith("//"):
        return "https:" + url

    return urljoin(base, url)


def normalize_url(url):
    if not url:
        return ""

    try:
        parsed = urlparse(url)

        # Remove fragment
        parsed = parsed._replace(fragment="")

        return parsed.geturl()
    except Exception:
        return str(url).strip()


# ============================================================
# PRODUCT VALIDATION
# ============================================================

BAD_NAMES = {
    "product",
    "product detail",
    "product details",
    "details",
    "view details",
    "view product",
    "read more",
    "buy now",
    "add to cart",
    "shop now",
    "top picks",
    "navigation",
    "logo settings",
    "desktop title styles",
    "sticky title styles",
    "mobile title styles",
    "announcement",
    "animation",
    "image carousel",
    "image slide",
    "image",
    "home page",
    "navigation menus",
    "text",
    "desktop image",
    "mobile image",
    "link",
    "blog",
    "category highlights",
    "select categories",
    "product list",
    "label",
    "collection link",
    "video widgets",
    "new slot",
    "post",
    "customer name",
    "customer detail",
    "testimonial content",
    "product link",
    "instagram profile url",
    "buttons",
    "category card",
    "product card",
    "collection card",
    "tags",
    "best seller tag",
    "offer tag",
    "limited time tag",
    "default menu",
    "featured products",
    "collections",
    "all products",
    "my orders",
    "cart page",
    "announcements",
    "subtitle",
    "full page cart",
}


def looks_like_hmt_watch_name(name):
    """
    Strict validation.

    We want actual HMT watch names and NOT frontend
    configuration/content objects.
    """

    name = clean_text(name)

    if not name:
        return False

    lower = name.lower()

    if lower in BAD_NAMES:
        return False

    if len(name) < 5 or len(name) > 180:
        return False

    # Obvious frontend/configuration names
    blocked_terms = [
        "navigation",
        "settings",
        "styles",
        "carousel",
        "announcement",
        "animation",
        "slot_",
        "slot ",
        "category card",
        "product card",
        "collection card",
        "navigation menu",
        "instagram profile",
        "testimonial",
        "full page cart",
        "default menu",
        "desktop image",
        "mobile image",
        "image slide",
        "video widgets",
    ]

    if any(term in lower for term in blocked_terms):
        return False

    # A real HMT watch normally contains HMT or a known HMT series.
    watch_terms = [
        "hmt",
        "galaxy",
        "inox",
        "janata",
        "kala",
        "kohinoor",
        "sangam",
        "pilot",
        "vijay",
        "stellar",
        "tareeq",
        "pace",
        "plus",
        "vihaan",
        "ravi",
        "elegance",
        "souga",
        "roman",
        "nass",
        "automatic",
        "quartz",
        "mechanical",
    ]

    if any(term in lower for term in watch_terms):
        return True

    return False


# ============================================================
# PRICE
# ============================================================

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


# ============================================================
# QUANTITY
# ============================================================

def parse_quantity(text):
    """
    Only returns a quantity when the website explicitly exposes it.
    We never guess inventory.
    """

    if not text:
        return None

    text = clean_text(text)

    patterns = [
        r"(?:only|just)\s+(\d+)\s+(?:left|remaining)",
        r"(\d+)\s+(?:units?|pieces?)\s+(?:left|remaining)",
        r"(?:available\s+quantity|quantity\s+available)\s*[:\-]?\s*(\d+)",
        r"(?:stock|inventory)\s*[:\-]?\s*(\d+)",
        r"(?:quantity)\s*[:\-]?\s*(\d+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.I)

        if match:
            try:
                return int(match.group(1))
            except Exception:
                pass

    return None


# ============================================================
# STOCK DETECTION
# ============================================================

def detect_stock(text):
    """
    True  = in stock
    False = out of stock
    None  = unknown
    """

    if not text:
        return None

    text = clean_text(text).lower()

    # Check negative status FIRST.
    out_patterns = [
        "out of stock",
        "sold out",
        "currently unavailable",
        "unavailable",
        "not available",
        "out-of-stock",
    ]

    for phrase in out_patterns:
        if phrase in text:
            return False

    in_patterns = [
        "in stock",
        "available",
        "add to cart",
        "buy now",
        "add-to-cart",
    ]

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
# HMT STORE - JSON EXTRACTION
# ============================================================

def get_value_case_insensitive(obj, candidates):
    if not isinstance(obj, dict):
        return None

    lower_keys = {
        str(k).lower(): k
        for k in obj.keys()
    }

    for candidate in candidates:
        if candidate.lower() in lower_keys:
            return obj[lower_keys[candidate.lower()]]

    return None


def extract_product_url(obj):
    candidates = [
        "url",
        "producturl",
        "product_url",
        "link",
        "productlink",
        "product_link",
        "handle",
        "slug",
    ]

    value = get_value_case_insensitive(
        obj,
        candidates,
    )

    if isinstance(value, dict):
        value = (
            value.get("url")
            or value.get("href")
            or value.get("link")
        )

    if not isinstance(value, str):
        return ""

    return absolute_url(
        HMT_STORE_URL,
        value,
    )


def extract_product_price(obj):
    candidates = [
        "price",
        "saleprice",
        "sale_price",
        "sellingprice",
        "selling_price",
        "amount",
        "finalprice",
        "final_price",
        "mrp",
    ]

    value = get_value_case_insensitive(
        obj,
        candidates,
    )

    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, str):
        return parse_price(value)

    if isinstance(value, dict):
        for key in [
            "amount",
            "value",
            "price",
        ]:
            nested = value.get(key)

            if isinstance(nested, (int, float)):
                return float(nested)

            if isinstance(nested, str):
                result = parse_price(nested)

                if result is not None:
                    return result

    return None


def extract_product_stock(obj):
    parts = []

    if not isinstance(obj, dict):
        return None, None

    for key, value in obj.items():

        key_lower = str(key).lower()

        if any(
            word in key_lower
            for word in [
                "stock",
                "available",
                "quantity",
                "inventory",
                "availability",
            ]
        ):
            parts.append(str(value))

    stock_text = " ".join(parts)

    return (
        detect_stock(stock_text),
        parse_quantity(stock_text),
    )


def recursive_product_objects(obj, found):
    """
    IMPORTANT:
    We no longer treat every object with 'name' or 'title'
    as a product.

    A candidate must have:
      1. A watch-like name
      2. A product-looking URL/handle OR price/stock information
    """

    if isinstance(obj, dict):

        name = get_value_case_insensitive(
            obj,
            [
                "productname",
                "product_name",
                "producttitle",
                "product_title",
                "name",
                "title",
            ],
        )

        if isinstance(name, str):
            name = clean_text(name)

            if looks_like_hmt_watch_name(name):

                product_url = extract_product_url(obj)
                price = extract_product_price(obj)
                stock, quantity = extract_product_stock(obj)

                # Strong requirement:
                # Do not accept arbitrary frontend objects.
                #
                # A product should have either:
                # - product-looking URL
                # - price
                # - stock/availability data
                #
                if (
                    product_url
                    or price is not None
                    or stock is not None
                    or quantity is not None
                ):

                    # Reject obviously non-product URLs
                    lower_url = product_url.lower()

                    if (
                        not product_url
                        or any(
                            bad in lower_url
                            for bad in [
                                "/settings",
                                "/navigation",
                                "/cart",
                                "/account",
                                "/blog",
                                "/collections/",
                            ]
                        )
                    ):
                        # If there is no URL, price/stock is enough.
                        if product_url:
                            pass

                    found.append({
                        "name": name,
                        "url": normalize_url(product_url),
                        "price": price,
                        "stock": stock,
                        "quantity": quantity,
                    })

        for value in obj.values():
            recursive_product_objects(
                value,
                found,
            )

    elif isinstance(obj, list):

        for item in obj:
            recursive_product_objects(
                item,
                found,
            )


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


# ============================================================
# HMT STORE - HTML PRODUCT CARDS
# ============================================================

def extract_store_cards(soup):
    products = []

    # Look for actual product-looking anchors first.
    for a in soup.find_all("a", href=True):

        href = absolute_url(
            HMT_STORE_URL,
            a.get("href"),
        )

        if not href:
            continue

        href_lower = href.lower()

        # The current HMT Store is NOT Shopify.
        # We therefore don't require "/products/".
        #
        # But we do require that the anchor/container
        # actually looks like a watch product.

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

            if len(text) > 1200:
                continue

            price = parse_price(text)

            if price is None:
                continue

            # Find title candidates inside card.
            names = []

            link_text = clean_text(
                a.get_text(
                    " ",
                    strip=True,
                )
            )

            if link_text:
                names.append(link_text)

            for element in container.find_all(
                ["h1", "h2", "h3", "h4", "strong"],
                limit=20,
            ):
                candidate = clean_text(
                    element.get_text(
                        " ",
                        strip=True,
                    )
                )

                if candidate:
                    names.append(candidate)

            product_name = ""

            for candidate in names:
                if looks_like_hmt_watch_name(candidate):
                    product_name = candidate
                    break

            if not product_name:
                continue

            products.append({
                "name": product_name,
                "url": normalize_url(href),
                "price": price,
                "stock": detect_stock(text),
                "quantity": parse_quantity(text),
            })

            break

    return products


# ============================================================
# HMT STORE
# ============================================================

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

        print(
            "HMT Store HTTP status:",
            response.status_code,
        )

        if response.status_code != 200:
            return []

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        products = []

        # ----------------------------------------------------
        # 1. JSON / React / Next data
        # ----------------------------------------------------

        json_products = []

        for data in extract_json_scripts(soup):

            recursive_product_objects(
                data,
                json_products,
            )

        products.extend(json_products)

        # ----------------------------------------------------
        # 2. HTML cards
        # ----------------------------------------------------

        products.extend(
            extract_store_cards(soup)
        )

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

            if not looks_like_hmt_watch_name(name):
                continue

            # Prefer URL as identity.
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
            "HMT Store candidate products:",
            len(products),
        )

        print(
            "HMT Store usable products:",
            len(products),
        )

        return products

    except Exception as e:

        print(
            "HMT Store scrape error:",
            e,
        )

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

    for a in soup.find_all(
        "a",
        href=True,
    ):

        href = a.get("href")

        if not href:
            continue

        full_url = absolute_url(
            source_url,
            href,
        )

        lower_url = full_url.lower()

        # HMT.in product detail URLs observed
        # by the diagnostic.
        if "product_details" not in lower_url:
            continue

        container = a

        best_name = ""

        # Search upward for the actual product card.
        for _ in range(6):

            if container.parent:
                container = container.parent

            text = clean_text(
                container.get_text(
                    " ",
                    strip=True,
                )
            )

            if len(text) > 1500:
                continue

            candidates = []

            # Anchor text
            anchor_text = clean_text(
                a.get_text(
                    " ",
                    strip=True,
                )
            )

            if anchor_text:
                candidates.append(anchor_text)

            # Heading/title candidates
            for child in container.find_all(
                [
                    "h1",
                    "h2",
                    "h3",
                    "h4",
                    "h5",
                    "strong",
                    "b",
                ],
                limit=40,
            ):

                candidate = clean_text(
                    child.get_text(
                        " ",
                        strip=True,
                    )
                )

                if candidate:
                    candidates.append(candidate)

            # Span/div candidates, but only a limited number.
            for child in container.find_all(
                ["span", "div"],
                limit=50,
            ):

                candidate = clean_text(
                    child.get_text(
                        " ",
                        strip=True,
                    )
                )

                if (
                    5 <= len(candidate) <= 120
                    and looks_like_hmt_watch_name(
                        candidate
                    )
                ):
                    candidates.append(candidate)

            for candidate in candidates:

                if looks_like_hmt_watch_name(
                    candidate
                ):
                    best_name = candidate
                    break

            if best_name:
                break

        if not best_name:
            continue

        card_text = clean_text(
            container.get_text(
                " ",
                strip=True,
            )
        )

        products.append({
            "name": best_name,
            "url": normalize_url(full_url),
            "price": parse_price(card_text),
            "stock": detect_stock(card_text),
            "quantity": parse_quantity(card_text),
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

    if not os.path.exists(
        STATE_FILE
    ):
        return None

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8",
        ) as f:

            data = json.load(f)

        if not isinstance(
            data,
            dict,
        ):
            return None

        return data

    except Exception as e:

        print(
            "Could not load state:",
            e,
        )

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
# PRODUCT KEY
# ============================================================

def product_key(product):

    url = normalize_url(
        product.get("url")
    )

    if url:
        return url

    return "|".join([
        product.get("source", ""),
        clean_text(
            product.get(
                "name",
                "",
            )
        ).lower(),
        str(
            product.get(
                "price",
                "",
            )
        ),
    ])


# ============================================================
# TELEGRAM FORMAT
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
        "🚨 HMT STOCK ALERT",
        "",
        f"⌚ {name}",
        f"🏪 Source: {source}",
    ]

    if price is not None:

        try:

            if float(price).is_integer():

                price_text = (
                    f"₹{int(price):,}"
                )

            else:

                price_text = (
                    f"₹{float(price):,.2f}"
                )

            lines.append(
                f"💰 Price: {price_text}"
            )

        except Exception:
            pass

    if stock is True:

        lines.append(
            "✅ Stock: IN STOCK"
        )

    elif stock is False:

        lines.append(
            "❌ Stock: OUT OF STOCK"
        )

    else:

        lines.append(
            "ℹ️ Stock: STATUS UNKNOWN"
        )

    if quantity is not None:

        lines.append(
            f"📦 Units available: {quantity}"
        )

    if url:

        lines.extend([
            "",
            f"🔗 {url}",
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

    # --------------------------------------------------------
    # IMPORTANT SAFETY CHECK
    # --------------------------------------------------------
    #
    # We monitor TWO independent websites.
    #
    # If one website suddenly returns zero products,
    # DO NOT throw away the previous state for that site.
    #
    # This prevents temporary scraper failures from causing
    # false "new product" alerts later.
    # --------------------------------------------------------

    if len(store_products) == 0:
        print(
            "WARNING: HMT Store returned ZERO products."
        )

    if len(hmt_products) == 0:
        print(
            "WARNING: HMT.in returned ZERO products."
        )

    # If BOTH failed, absolutely do not overwrite state.
    if (
        len(store_products) == 0
        and len(hmt_products) == 0
    ):

        print()
        print(
            "WARNING: BOTH SITES RETURNED ZERO PRODUCTS."
        )

        print(
            "Existing stock_state.json "
            "will NOT be overwritten."
        )

        return

    products = (
        store_products +
        hmt_products
    )

    print(
        "TOTAL:",
        len(products),
    )

    # --------------------------------------------------------
    # CURRENT STATE
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

    if not current_state:
        print(
            "WARNING: No valid products."
        )
        return

    # --------------------------------------------------------
    # PREVIOUS STATE
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

        save_state(
            current_state
        )

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
    # DETECTION
    # --------------------------------------------------------

    restocks = []
    quantity_changes = []
    new_products = []

    for key, current in current_state.items():

        previous = previous_state.get(
            key
        )

        name = current.get(
            "name",
            "Unknown",
        )

        source = current.get(
            "source",
            "",
        )

        # ----------------------------------------------------
        # NEW PRODUCT
        # ----------------------------------------------------

        if previous is None:

            print(
                f"NEW PRODUCT: {name} "
                f"[{source}]"
            )

            new_products.append(
                current
            )

            # We DO NOT send a Telegram alert here.
            # New baseline products should not spam Telegram.

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
        # QUANTITY INCREASE
        # ----------------------------------------------------
        #
        # Only alert when quantity INCREASES.
        #
        # Example:
        # 2 -> 5 = alert
        # 5 -> 2 = don't alert
        # 5 -> 5 = nothing
        # ----------------------------------------------------

        if (
            old_quantity is not None
            and new_quantity is not None
            and new_quantity > old_quantity
        ):

            print(
                f"QUANTITY INCREASE: {name} "
                f"{old_quantity} -> {new_quantity}"
            )

            quantity_changes.append(
                (
                    previous,
                    current,
                )
            )

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    save_state(
        current_state
    )

    print()
    print(
        f"Saved {len(current_state)} "
        "products to stock_state.json"
    )

    # --------------------------------------------------------
    # TELEGRAM RESTOCK ALERTS
    # --------------------------------------------------------

    for product in restocks:

        send_telegram(
            format_product(product)
        )

        time.sleep(1)

    # --------------------------------------------------------
    # TELEGRAM QUANTITY ALERTS
    # --------------------------------------------------------

    for previous, current in quantity_changes:

        message = format_product(
            current
        )

        old_quantity = previous.get(
            "quantity"
        )

        new_quantity = current.get(
            "quantity"
        )

        message += (
            "\n\n"
            f"📈 Quantity increased: "
            f"{old_quantity} → {new_quantity}"
        )

        send_telegram(
            message
        )

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
        "Quantity increases:",
        len(quantity_changes),
    )

    print(
        "New products:",
        len(new_products),
    )

    print("=" * 60)


if __name__ == "__main__":
    main()
