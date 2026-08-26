import os
import re
import json
import time
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup


# ============================================================
# CONFIG
# ============================================================

HMT_STORE = "https://www.hmtwatches.store"
HMT_STORE_ALL = f"{HMT_STORE}/collections/all"

HMT_IN = "https://www.hmtwatches.in"
HMT_IN_ALL = f"{HMT_IN}/all_product"

STATE_FILE = "stock_state.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TIMEOUT = 25

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
    "Cache-Control": "no-cache",
}


# ============================================================
# HELPERS
# ============================================================

session = requests.Session()
session.headers.update(HEADERS)


def clean_text(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def absolute_url(url, base):
    if not url:
        return ""
    return urljoin(base, url)


def canonical_url(url):
    """
    Remove fragments and tracking parameters while preserving
    the actual product identifier.
    """
    if not url:
        return ""

    parsed = urlparse(url)

    if "hmtwatches.in" in parsed.netloc:
        if parsed.path == "/product_overview":
            query = parse_qs(parsed.query)
            product_id = query.get("id", [""])[0]
            if product_id:
                return (
                    "https://www.hmtwatches.in/product_overview"
                    "?id=" + product_id
                )

    if "hmtwatches.store" in parsed.netloc:
        return (
            f"{parsed.scheme}://{parsed.netloc}"
            f"{parsed.path.rstrip('/')}"
        )

    return url.split("#")[0]


def money_to_number(value):
    if value is None:
        return None

    text = clean_text(value)
    text = text.replace(",", "")

    match = re.search(r"(?:₹|Rs\.?|INR)?\s*([0-9]+(?:\.[0-9]+)?)", text)

    if not match:
        return None

    try:
        number = float(match.group(1))
        return int(number) if number.is_integer() else number
    except Exception:
        return None


def find_quantity(text):
    """
    Attempts to find a real inventory quantity if the website
    exposes one. Does NOT invent a quantity from availability.
    """
    if not text:
        return None

    patterns = [
        r'"inventory_quantity"\s*:\s*(\d+)',
        r'"inventoryQuantity"\s*:\s*(\d+)',
        r'"quantity"\s*:\s*(\d+)',
        r'"stock"\s*:\s*(\d+)',
        r'"stock_quantity"\s*:\s*(\d+)',
        r'"available_quantity"\s*:\s*(\d+)',
        r'"availableQuantity"\s*:\s*(\d+)',
        r'quantityAvailable["\']?\s*[:=]\s*(\d+)',
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            try:
                return int(match.group(1))
            except Exception:
                pass

    return None


def looks_like_watch(name):
    """
    Prevents collection/category/navigation records from becoming
    fake products.
    """
    if not name:
        return False

    name = clean_text(name)

    bad = {
        "top picks",
        "featured products",
        "all products",
        "products",
        "watches",
        "mens watches",
        "women watches",
        "for him",
        "for her",
        "collections",
    }

    return name.lower() not in bad and len(name) >= 4


def availability_from_text(text):
    """
    Returns:
        True  = in stock
        False = out of stock
        None  = unknown
    """
    if not text:
        return None

    t = clean_text(text).lower()

    out_patterns = [
        "out of stock",
        "sold out",
        "notify me",
        "currently unavailable",
        "unavailable",
    ]

    in_patterns = [
        "add to cart",
        "buy now",
        "add to bag",
        "in stock",
    ]

    for phrase in out_patterns:
        if phrase in t:
            return False

    for phrase in in_patterns:
        if phrase in t:
            return True

    return None


# ============================================================
# HMT STORE
# ============================================================

def extract_store_product_links(html):
    soup = BeautifulSoup(html, "html.parser")
    results = set()

    # Normal links
    for a in soup.find_all("a", href=True):
        href = absolute_url(a.get("href"), HMT_STORE)

        if "/product/" in href:
            results.add(canonical_url(href))

    # Raw HTML fallback
    regexes = [
        r'https?://(?:www\.)?hmtwatches\.store/product/[A-Za-z0-9\-]+',
        r'/product/[A-Za-z0-9\-]+',
    ]

    for pattern in regexes:
        for match in re.findall(pattern, html, re.I):
            results.add(canonical_url(absolute_url(match, HMT_STORE)))

    return {x for x in results if "/product/" in x}


def extract_store_json_products(obj, results):
    """
    Recursively searches Next.js / embedded JSON for product-like
    objects. This is useful because the current HMT Store page
    does not always expose normal product links in the HTML.
    """

    if isinstance(obj, dict):
        lower_keys = {str(k).lower() for k in obj.keys()}

        possible_name = None
        possible_url = None
        possible_price = None
        possible_available = None
        possible_quantity = None

        for key, value in obj.items():
            lk = str(key).lower()

            if lk in {"name", "title", "productname", "product_name"}:
                if isinstance(value, str):
                    possible_name = clean_text(value)

            elif lk in {"url", "handle", "producturl", "product_url", "link"}:
                if isinstance(value, str):
                    possible_url = value

            elif lk in {"price", "sellingprice", "saleprice", "mrp"}:
                if possible_price is None:
                    possible_price = money_to_number(value)

            elif lk in {
                "available",
                "availableforpurchase",
                "instock",
                "in_stock",
            }:
                if isinstance(value, bool):
                    possible_available = value

            elif lk in {
                "inventory_quantity",
                "inventoryquantity",
                "quantity",
                "stock",
                "stock_quantity",
                "available_quantity",
                "availablequantity",
            }:
                try:
                    possible_quantity = int(value)
                except Exception:
                    pass

        if (
            possible_name
            and looks_like_watch(possible_name)
            and possible_url
        ):
            full = absolute_url(possible_url, HMT_STORE)

            if "/product/" in full:
                results.append(
                    {
                        "name": possible_name,
                        "url": canonical_url(full),
                        "price": possible_price,
                        "available": possible_available,
                        "quantity": possible_quantity,
                    }
                )

        for value in obj.values():
            extract_store_json_products(value, results)

    elif isinstance(obj, list):
        for item in obj:
            extract_store_json_products(item, results)


def scrape_hmt_store():
    print("=" * 42)
    print("SCRAPING HMT STORE")
    print("=" * 42)

    products = {}

    try:
        response = session.get(
            HMT_STORE_ALL,
            timeout=TIMEOUT,
        )

        print("HMT Store HTTP status:", response.status_code)

        if response.status_code != 200:
            return []

        html = response.text

        # ----------------------------------------------------
        # Method 1: normal product links
        # ----------------------------------------------------
        links = extract_store_product_links(html)

        # ----------------------------------------------------
        # Method 2: embedded JSON
        # ----------------------------------------------------
        json_products = []

        soup = BeautifulSoup(html, "html.parser")

        for script in soup.find_all("script"):
            script_text = script.string or script.get_text()

            if not script_text:
                continue

            script_text = script_text.strip()

            if not script_text:
                continue

            if script.get("type") == "application/ld+json":
                try:
                    data = json.loads(script_text)
                    extract_store_json_products(
                        data,
                        json_products,
                    )
                except Exception:
                    pass

            elif (
                "__NEXT_DATA__" in script_text
                or "product" in script_text.lower()
            ):
                # Try JSON parsing first
                try:
                    data = json.loads(script_text)
                    extract_store_json_products(
                        data,
                        json_products,
                    )
                except Exception:
                    pass

                # Search raw script for product URLs
                for match in re.findall(
                    r'/product/[A-Za-z0-9\-]+',
                    script_text,
                    re.I,
                ):
                    links.add(
                        canonical_url(
                            absolute_url(match, HMT_STORE)
                        )
                    )

        # Add discovered JSON URLs
        for p in json_products:
            if p.get("url"):
                links.add(p["url"])

        print("HMT Store candidate products:", len(links))

        # ----------------------------------------------------
        # Fetch individual product pages
        # ----------------------------------------------------
        for url in sorted(links):

            try:
                r = session.get(
                    url,
                    timeout=TIMEOUT,
                )

                if r.status_code != 200:
                    continue

                soup = BeautifulSoup(
                    r.text,
                    "html.parser",
                )

                text = clean_text(
                    soup.get_text(" ", strip=True)
                )

                name = ""

                # JSON-LD product name
                for script in soup.find_all(
                    "script",
                    type="application/ld+json",
                ):
                    try:
                        data = json.loads(
                            script.string or script.get_text()
                        )

                        candidates = (
                            data
                            if isinstance(data, list)
                            else [data]
                        )

                        for item in candidates:
                            if (
                                isinstance(item, dict)
                                and str(
                                    item.get("@type", "")
                                ).lower()
                                == "product"
                            ):
                                name = clean_text(
                                    item.get("name")
                                )
                                break
                    except Exception:
                        pass

                if not name:
                    title = soup.find("title")

                    if title:
                        name = clean_text(title.get_text())

                if not name:
                    h1 = soup.find("h1")

                    if h1:
                        name = clean_text(h1.get_text())

                # Remove generic site suffixes
                name = re.sub(
                    r"\s*\|\s*HMT.*$",
                    "",
                    name,
                    flags=re.I,
                )

                name = clean_text(name)

                if not looks_like_watch(name):
                    continue

                price = None

                # Search visible text
                price_patterns = [
                    r"₹\s*[\d,]+(?:\.\d+)?",
                    r"Rs\.?\s*[\d,]+(?:\.\d+)?",
                ]

                for pattern in price_patterns:
                    match = re.search(
                        pattern,
                        text,
                        re.I,
                    )
                    if match:
                        price = money_to_number(
                            match.group(0)
                        )
                        break

                available = availability_from_text(text)
                quantity = find_quantity(r.text)

                products[canonical_url(url)] = {
                    "name": name,
                    "url": canonical_url(url),
                    "source": "HMT Store",
                    "available": available,
                    "quantity": quantity,
                    "price": price,
                }

            except Exception as e:
                print(
                    "Store product error:",
                    str(e)[:120],
                )

        print(
            "HMT Store usable products:",
            len(products),
        )

    except Exception as e:
        print("HMT Store scrape error:", e)

    return list(products.values())


# ============================================================
# HMT.IN
# ============================================================

def extract_hmt_in_product_links(html):
    soup = BeautifulSoup(html, "html.parser")

    results = set()

    # --------------------------------------------------------
    # Actual current HMT.in product URL format:
    # /product_overview?id=...
    # --------------------------------------------------------
    for a in soup.find_all("a", href=True):
        href = absolute_url(
            a.get("href"),
            HMT_IN,
        )

        if (
            "/product_overview" in href
            or "/product_details" in href
        ):
            results.add(canonical_url(href))

    # Raw HTML fallback
    patterns = [
        r'https?://(?:www\.)?hmtwatches\.in/product_overview\?id=[^"\'&<>\s]+',
        r'/product_overview\?id=[^"\'&<>\s]+',
        r'https?://(?:www\.)?hmtwatches\.in/product_details\?id=[^"\'&<>\s]+',
        r'/product_details\?id=[^"\'&<>\s]+',
    ]

    for pattern in patterns:
        for match in re.findall(
            pattern,
            html,
            re.I,
        ):
            results.add(
                canonical_url(
                    absolute_url(
                        match,
                        HMT_IN,
                    )
                )
            )

    return results


def scrape_hmt_in():
    print("=" * 42)
    print("SCRAPING HMT.IN")
    print("=" * 42)

    products = {}

    pages_to_try = [
        HMT_IN_ALL,
        f"{HMT_IN}/watches",
        f"{HMT_IN}/mens",
        f"{HMT_IN}/womens",
    ]

    all_links = set()

    try:
        for page_url in pages_to_try:

            try:
                response = session.get(
                    page_url,
                    timeout=TIMEOUT,
                )

                print(
                    f"HMT.in HTTP status "
                    f"({page_url}):",
                    response.status_code,
                )

                if response.status_code != 200:
                    continue

                links = extract_hmt_in_product_links(
                    response.text
                )

                all_links.update(links)

            except Exception as e:
                print(
                    "HMT.in page error:",
                    str(e)[:120],
                )

        print(
            "HMT.in product links found:",
            len(all_links),
        )

        # ----------------------------------------------------
        # Product pages
        # ----------------------------------------------------
        for url in sorted(all_links):

            try:
                response = session.get(
                    url,
                    timeout=TIMEOUT,
                )

                if response.status_code != 200:
                    continue

                html = response.text

                soup = BeautifulSoup(
                    html,
                    "html.parser",
                )

                text = clean_text(
                    soup.get_text(
                        " ",
                        strip=True,
                    )
                )

                # --------------------------------------------
                # Product name
                # --------------------------------------------
                name = ""

                # HMT product pages normally expose the name
                # in an H1.
                h1 = soup.find("h1")

                if h1:
                    name = clean_text(
                        h1.get_text()
                    )

                if not name:
                    # JSON-LD fallback
                    for script in soup.find_all(
                        "script",
                        type="application/ld+json",
                    ):
                        try:
                            data = json.loads(
                                script.string
                                or script.get_text()
                            )

                            candidates = (
                                data
                                if isinstance(data, list)
                                else [data]
                            )

                            for item in candidates:
                                if (
                                    isinstance(item, dict)
                                    and str(
                                        item.get("@type", "")
                                    ).lower()
                                    == "product"
                                ):
                                    name = clean_text(
                                        item.get("name")
                                    )
                                    break

                            if name:
                                break

                        except Exception:
                            pass

                if not name:
                    title = soup.find("title")

                    if title:
                        name = clean_text(
                            title.get_text()
                        )

                name = re.sub(
                    r"\s*\|\s*.*$",
                    "",
                    name,
                )

                name = clean_text(name)

                if not looks_like_watch(name):
                    continue

                # --------------------------------------------
                # Price
                # --------------------------------------------
                price = None

                price_patterns = [
                    r"₹\s*[\d,]+(?:\.\d+)?",
                    r"Rs\.?\s*[\d,]+(?:\.\d+)?",
                    r"MRP\s*[\d,]+(?:\.\d+)?",
                ]

                for pattern in price_patterns:
                    match = re.search(
                        pattern,
                        text,
                        re.I,
                    )

                    if match:
                        price = money_to_number(
                            match.group(0)
                        )
                        break

                # --------------------------------------------
                # Availability
                # --------------------------------------------
                available = availability_from_text(
                    text
                )

                # --------------------------------------------
                # Quantity
                # --------------------------------------------
                quantity = find_quantity(html)

                products[canonical_url(url)] = {
                    "name": name,
                    "url": canonical_url(url),
                    "source": "HMT.in",
                    "available": available,
                    "quantity": quantity,
                    "price": price,
                }

            except Exception as e:
                print(
                    "HMT.in product error:",
                    str(e)[:120],
                )

        print(
            "HMT.in usable products:",
            len(products),
        )

    except Exception as e:
        print("HMT.in scrape error:", e)

    return list(products.values())


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
        print("Could not load state:", e)

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

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(
            "Telegram credentials not configured."
        )
        return False

    url = (
        "https://api.telegram.org/bot"
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

        if response.status_code == 200:
            print("Telegram notification sent.")
            return True

        print(
            "Telegram error:",
            response.status_code,
            response.text[:300],
        )

    except Exception as e:
        print(
            "Telegram request error:",
            e,
        )

    return False


# ============================================================
# ALERT FORMAT
# ============================================================

def format_product(product):
    name = product.get("name", "Unknown")
    source = product.get("source", "Unknown")
    url = product.get("url", "")
    price = product.get("price")
    quantity = product.get("quantity")

    lines = [
        f"⌚ {name}",
        f"Source: {source}",
    ]

    if price is not None:
        lines.append(
            f"Price: ₹{price}"
        )

    if quantity is not None:
        lines.append(
            f"Stock quantity: {quantity}"
        )
    else:
        lines.append(
            "Stock quantity: Not exposed by site"
        )

    lines.append("")
    lines.append(url)

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)
    print("HMT WATCH RESTOCK TRACKER")
    print("=" * 60)

    old_state = load_state()

    # --------------------------------------------------------
    # SCRAPE
    # --------------------------------------------------------

    store_products = scrape_hmt_store()
    hmt_products = scrape_hmt_in()

    products = store_products + hmt_products

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
    # Safety mechanism
    # --------------------------------------------------------

    if len(products) == 0:

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
    # PROCESS
    # --------------------------------------------------------

    print()
    print(
        f"Processing {len(products)} products..."
    )

    new_state = {}

    restocks = []
    quantity_changes = []
    new_products = []

    for product in products:

        key = canonical_url(
            product["url"]
        )

        product["url"] = key

        current_available = product.get(
            "available"
        )

        current_quantity = product.get(
            "quantity"
        )

        previous = old_state.get(key)

        # ----------------------------------------------------
        # First time seeing product
        # ----------------------------------------------------

        if previous is None:

            print(
                f"BASELINE: "
                f"{product['name'][:45]} "
                f"[{product['source']}]"
            )

            new_products.append(product)

        else:

            old_available = previous.get(
                "available"
            )

            old_quantity = previous.get(
                "quantity"
            )

            # ------------------------------------------------
            # RESTOCK
            # ------------------------------------------------

            if (
                old_available is False
                and current_available is True
            ):
                restocks.append(product)

            # ------------------------------------------------
            # QUANTITY CHANGE
            # ------------------------------------------------

            if (
                old_quantity is not None
                and current_quantity is not None
                and old_quantity != current_quantity
            ):
                quantity_changes.append(
                    (
                        product,
                        old_quantity,
                        current_quantity,
                    )
                )

        new_state[key] = {
            "name": product.get("name"),
            "url": product.get("url"),
            "source": product.get("source"),
            "available": product.get("available"),
            "quantity": product.get("quantity"),
            "price": product.get("price"),
        }

    # --------------------------------------------------------
    # SAVE STATE
    # --------------------------------------------------------

    save_state(new_state)

    print()
    print(
        f"Saved {len(new_state)} products "
        f"to {STATE_FILE}"
    )

    # --------------------------------------------------------
    # ALERTS
    # --------------------------------------------------------

    messages = []

    for product in restocks:

        messages.append(
            "🚨 HMT WATCH RESTOCKED\n\n"
            + format_product(product)
        )

    for product, old_qty, new_qty in quantity_changes:

        messages.append(
            "📦 HMT STOCK QUANTITY CHANGED\n\n"
            + format_product(product)
            + "\n\n"
            + f"Previous quantity: {old_qty}\n"
            + f"Current quantity: {new_qty}"
        )

    # --------------------------------------------------------
    # New product alerts
    #
    # Do NOT alert on first baseline.
    # Only future newly discovered products are alerts.
    # --------------------------------------------------------

    # The first run creates a baseline.
    # New product detection therefore starts on the next run.

    if old_state:

        for product in new_products:

            messages.append(
                "🆕 NEW HMT WATCH FOUND\n\n"
                + format_product(product)
            )

    for message in messages:

        send_telegram(message)

        # Avoid hammering Telegram if many products change.
        time.sleep(0.5)

    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------

    print()
    print("=" * 60)
    print("RUN COMPLETE")
    print("=" * 60)

    print(
        "Products monitored:",
        len(new_state),
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
        len(new_products)
        if old_state
        else 0,
    )

    print("=" * 60)


if __name__ == "__main__":
    main()
