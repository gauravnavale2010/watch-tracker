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
        parsed = parsed._replace(fragment="")
        return parsed.geturl()
    except Exception:
        return str(url).strip()


# ============================================================
# PRODUCT NAME VALIDATION
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

    # HMT Store frontend configuration
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
    name = clean_text(name)

    if not name:
        return False

    lower = name.lower()

    if lower in BAD_NAMES:
        return False

    if len(name) < 5 or len(name) > 180:
        return False

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

    # Galaxy and INOX intentionally included.
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
        "utsav",
        "sona",
        "swarna",
        "amrut",
        "economic",
        "chronograph",
        "vivek",
        "bahadur",
        "commando",
        "gandaberunda",
        "jawan",
        "operation sindoor",
    ]

    return any(term in lower for term in watch_terms)


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
# STOCK
# ============================================================

def detect_stock(text):
    if not text:
        return None

    text = clean_text(text).lower()

    # Negative status FIRST.
    out_patterns = [
        "out of stock",
        "sold out",
        "currently unavailable",
        "unavailable",
        "not available",
        "out-of-stock",
        "coming soon",
    ]

    for phrase in out_patterns:
        if phrase in text:
            return False

    in_patterns = [
        "in stock",
        "add to cart",
        "buy now",
        "add-to-cart",
        "available",
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
# HMT STORE
# ============================================================

def extract_store_products(soup):
    products = []

    # --------------------------------------------------------
    # Look through anchors and nearby product cards.
    # --------------------------------------------------------

    for a in soup.find_all("a", href=True):

        href = absolute_url(
            HMT_STORE_URL,
            a.get("href"),
        )

        if not href:
            continue

        container = a

        for _ in range(5):

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

            price = parse_price(text)

            if price is None:
                continue

            candidates = []

            anchor_text = clean_text(
                a.get_text(
                    " ",
                    strip=True,
                )
            )

            if anchor_text:
                candidates.append(anchor_text)

            for element in container.find_all(
                ["h1", "h2", "h3", "h4", "h5", "strong", "b"],
                limit=30,
            ):

                candidate = clean_text(
                    element.get_text(
                        " ",
                        strip=True,
                    )
                )

                if candidate:
                    candidates.append(candidate)

            product_name = ""

            for candidate in candidates:

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

        products = extract_store_products(soup)

        # Deduplicate.
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

            key = url if url else (
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
#
# IMPORTANT:
# The current HMT.in listing pages expose the product NAME,
# PRICE and STOCK STATUS directly in the rendered listing.
#
# We therefore do NOT depend on product_details URLs.
# ============================================================

def find_product_name_in_container(container):

    # First try semantic/title elements.
    for tag in [
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "strong",
        "b",
    ]:

        for element in container.find_all(
            tag,
            limit=50,
        ):

            candidate = clean_text(
                element.get_text(
                    " ",
                    strip=True,
                )
            )

            if looks_like_hmt_watch_name(candidate):
                return candidate

    # Then inspect links.
    for element in container.find_all(
        "a",
        limit=50,
    ):

        candidate = clean_text(
            element.get_text(
                " ",
                strip=True,
            )
        )

        if looks_like_hmt_watch_name(candidate):
            return candidate

    # Finally inspect reasonably small spans/divs.
    for element in container.find_all(
        ["span", "div", "p"],
        limit=100,
    ):

        candidate = clean_text(
            element.get_text(
                " ",
                strip=True,
            )
        )

        if (
            5 <= len(candidate) <= 120
            and looks_like_hmt_watch_name(candidate)
        ):
            return candidate

    return ""


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
    # STRATEGY 1
    #
    # Find every visible text element that looks like an HMT
    # watch name, then climb to the smallest useful container
    # containing a price/status.
    # --------------------------------------------------------

    candidates = []

    for element in soup.find_all(
        ["a", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "b", "span"],
    ):

        text = clean_text(
            element.get_text(
                " ",
                strip=True,
            )
        )

        if looks_like_hmt_watch_name(text):

            candidates.append(element)

    # Remove duplicate DOM elements by text + position-ish identity.
    seen_names = set()

    for element in candidates:

        name = clean_text(
            element.get_text(
                " ",
                strip=True,
            )
        )

        name_key = name.lower()

        if name_key in seen_names:
            continue

        seen_names.add(name_key)

        container = element

        best_container = None

        # Climb only a few levels.
        for _ in range(7):

            if container.parent:
                container = container.parent

            text = clean_text(
                container.get_text(
                    " ",
                    strip=True,
                )
            )

            if len(text) > 1000:
                continue

            price = parse_price(text)

            if price is not None:

                # This is likely the product card.
                best_container = container
                break

        if best_container is None:
            continue

        card_text = clean_text(
            best_container.get_text(
                " ",
                strip=True,
            )
        )

        price = parse_price(card_text)

        if price is None:
            continue

        stock = detect_stock(card_text)

        # Try to find an actual link in the card.
        product_url = ""

        for link in best_container.find_all(
            "a",
            href=True,
        ):

            href = absolute_url(
                source_url,
                link.get("href"),
            )

            if href:
                lower = href.lower()

                # Reject obvious navigation URLs.
                if any(
                    bad in lower
                    for bad in [
                        "/login",
                        "/register",
                        "/cart",
                        "/wishlist",
                        "/contact",
                        "/about",
                        "/faq",
                    ]
                ):
                    continue

                product_url = normalize_url(href)

                # Prefer links that aren't generic navigation.
                if (
                    "product" in lower
                    or "detail" in lower
                    or "watch" in lower
                ):
                    break

        products.append({
            "name": name,
            "url": product_url,
            "price": price,
            "stock": stock,
            "quantity": parse_quantity(card_text),
        })

    # --------------------------------------------------------
    # STRATEGY 2
    #
    # Some versions of the site use repeated cards without
    # useful semantic headings. Search generic containers
    # containing a watch name + price.
    # --------------------------------------------------------

    for container in soup.find_all(
        ["article", "li"],
    ):

        text = clean_text(
            container.get_text(
                " ",
                strip=True,
            )
        )

        if len(text) > 1000:
            continue

        price = parse_price(text)

        if price is None:
            continue

        name = find_product_name_in_container(
            container
        )

        if not name:
            continue

        product_url = ""

        for link in container.find_all(
            "a",
            href=True,
        ):

            href = absolute_url(
                source_url,
                link.get("href"),
            )

            if href:
                product_url = normalize_url(href)
                break

        products.append({
            "name": name,
            "url": product_url,
            "price": price,
            "stock": detect_stock(text),
            "quantity": parse_quantity(text),
        })

    return products


def deduplicate_hmt_in(products):

    unique = {}

    for product in products:

        name = clean_text(
            product.get("name")
        )

        if not looks_like_hmt_watch_name(name):
            continue

        url = normalize_url(
            product.get("url")
        )

        price = product.get("price")

        # URL is best identity.
        #
        # If the current HMT site does not expose a URL,
        # name + price is used.
        if url:
            key = url
        else:
            key = (
                name.lower(),
                price,
            )

        # Prefer a version that has a URL.
        if key not in unique:

            product["name"] = name
            product["url"] = url

            unique[key] = product

        else:

            existing = unique[key]

            if (
                not existing.get("url")
                and url
            ):
                existing["url"] = url

            if (
                existing.get("price") is None
                and price is not None
            ):
                existing["price"] = price

            if (
                existing.get("stock") is None
                and product.get("stock") is not None
            ):
                existing["stock"] = product.get("stock")

    return list(unique.values())


def scrape_hmt_in():

    print("=" * 42)
    print("SCRAPING HMT.IN")
    print("=" * 42)

    all_products = []

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

            products = deduplicate_hmt_in(
                products
            )

            print(
                "  Products extracted from page:",
                len(products),
            )

            all_products.extend(products)

            time.sleep(0.5)

        except Exception as e:

            print(
                f"HMT.in error ({url}):",
                e,
            )

    all_products = deduplicate_hmt_in(
        all_products
    )

    print(
        "HMT.in product links found:",
        len([
            p for p in all_products
            if p.get("url")
        ]),
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

    price = product.get("price")
    stock = product.get("stock")
    quantity = product.get("quantity")

    lines = [
        "🚨 HMT STOCK ALERT",
        "",
        f"⌚ {name}",
        f"🏪 Source: {source}",
    ]

    if price is not None:

        try:

            if float(price).is_integer():
                price_text = f"₹{int(price):,}"
            else:
                price_text = f"₹{float(price):,.2f}"

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

    if len(store_products) == 0:
        print(
            "WARNING: HMT Store returned ZERO products."
        )

    if len(hmt_products) == 0:
        print(
            "WARNING: HMT.in returned ZERO products."
        )

    # If BOTH sites fail, never overwrite state.
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
            "Quantity increases: 0"
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

        previous = previous_state.get(key)

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

            # No alert for new products.
            continue

        old_stock = previous.get("stock")
        new_stock = current.get("stock")

        old_quantity = previous.get("quantity")
        new_quantity = current.get("quantity")

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
    # SAVE STATE
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
