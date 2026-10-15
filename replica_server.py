#!/usr/bin/env python3
"""
Local replica of a messy e-commerce catalog, for testing scrapers safely.

Serves 120,000 synthetic products over 2,400 paginated pages with:
  * obfuscated class names, split tags, ad slots, struck-through old prices
  * mixed price formats ("$1,299.00" / "1,299.00 USD"), missing prices ("N/A")
  * stock text variants ("In stock (12)", "Only 3 left", "Out of stock")
  * "sponsored" duplicates of earlier products on every 4th page
  * random latency, HTTP 429 (with Retry-After) and HTTP 500 responses

All data is synthetic. Nothing here touches a real website.

Run:  python replica_server.py --port 8765
"""
import argparse
import html
import json
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TOTAL_PRODUCTS = 120_000
PER_PAGE = 50
TOTAL_PAGES = TOTAL_PRODUCTS // PER_PAGE  # 2,400

P_429 = 0.04            # share of requests answered with 429 Too Many Requests
P_500 = 0.015           # share of requests answered with 500 Internal Server Error
LATENCY = (0.015, 0.070)  # seconds, uniform

BRANDS = ["Aurora", "Nimbus", "Kestrel", "Vertex", "Lumen", "Orbit", "Tandem",
          "Quill", "Harbor", "Zephyr", "Cobalt", "Ember"]
ADJECTIVES = ["Compact", "Pro", "Ultra", "Classic", "Smart", "Eco", "Slim",
              "Turbo", "Prime", "Lite"]
NOUNS = ["Headphones", "Backpack", "Desk Lamp", "Bottle", "Keyboard", "Stand",
         "Webcam", "Speaker", "Notebook", "Mouse", "Charger", "Router"]
CATEGORIES = ["Audio", "Bags", "Home Office", "Outdoors", "Computing",
              "Accessories", "Networking"]

STATS = {"requests": 0, "http_200": 0, "http_429": 0, "http_500": 0}
STATS_LOCK = threading.Lock()


def make_product(pid):
    """Deterministic synthetic product: the same id always gives the same data."""
    r = random.Random(pid * 7919 + 13)
    name = f"{r.choice(BRANDS)} {r.choice(ADJECTIVES)} {r.choice(NOUNS)} {r.randint(100, 999)}"
    if r.random() < 0.002:
        name = ""  # broken listing
    price = min(2500.0, max(1.0, round(r.lognormvariate(3.6, 0.9), 2)))
    roll = r.random()
    if roll < 0.006:
        price_text = "N/A"
    elif roll < 0.5:
        price_text = f"${price:,.2f}"
    else:
        price_text = f"{price:,.2f} USD"
    old_price = f"${price * 1.2:,.2f}" if r.random() < 0.3 else ""
    roll = r.random()
    if roll < 0.12:
        stock_text = "Out of stock"
    elif roll < 0.25:
        stock_text = f"Only {r.randint(1, 5)} left"
    else:
        stock_text = f"In stock ({r.randint(1, 80)})"
    if r.random() < 0.1:
        rating_text = "No ratings yet"
    else:
        rating_text = f"{r.uniform(1.5, 5.0):.1f} / 5 ({r.randint(1, 900)} reviews)"
    return {"sku": f"SKU-{pid:06d}", "pid": pid, "name": name, "price_text": price_text,
            "old_price": old_price, "stock_text": stock_text, "rating_text": rating_text,
            "category": r.choice(CATEGORIES)}


def card_html(pid):
    p = make_product(pid)
    lines = [
        '<div class="c_x9 grid-item"',
        f' data-sku="{p["sku"]}">',
        f'<a class="t_l1" href="/item/{pid}">',
        '<span class="n">',
        html.escape(p["name"]),
        '</span></a>',
        '<div class="pr_w">',
        f'<span class="p_a">{p["price_text"]}</span>',
    ]
    if p["old_price"]:
        lines.append(f'<s class="p_o">{p["old_price"]}</s>')
    lines += [
        '</div>',
        '<div class="st_k">',
        p["stock_text"],
        '</div>',
        '<div class="rt_v">',
        p["rating_text"],
        '</div>',
        f'<span class="tag">{p["category"]}</span>',
        '</div>',
    ]
    return "\n".join(lines)


def page_ids(page):
    start = (page - 1) * PER_PAGE + 1
    ids = list(range(start, start + PER_PAGE))
    if page % 4 == 0 and page > 4:  # a "sponsored" repeat of an earlier product
        rr = random.Random(page)
        ids.insert(rr.randint(0, len(ids)), rr.randint(1, (page - 1) * PER_PAGE))
    return ids


def render_page(page):
    parts = []
    for n, pid in enumerate(page_ids(page)):
        parts.append(card_html(pid))
        if n % 17 == 16:
            parts.append('<div class="ad-slot x2"><!-- sponsored --></div>')
    nav = ""
    if page > 1:
        nav += f'<a class="pg prev" href="/products?page={page - 1}">Prev</a> '
    if page < TOTAL_PAGES:
        nav += f'<a class="pg next" href="/products?page={page + 1}">Next</a>'
    body = "\n".join(parts)
    return (
        '<!doctype html>\n<html><head><meta charset="utf-8">'
        f'<title>Catalog - page {page}</title></head>\n<body>\n'
        f'<div id="app" data-total-pages="{TOTAL_PAGES}" data-page="{page}">\n'
        f'<div class="grid">\n{body}\n</div>\n'
        f'<div class="pager">{nav}<span class="pn">Page {page} of {TOTAL_PAGES}</span></div>\n'
        '</div>\n</body></html>\n'
    )


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep the console quiet
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client went away (e.g. a killed scraper); nothing to do

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/health":
            return self._send(200, "ok", "text/plain")
        if url.path == "/robots.txt":
            return self._send(200, "User-agent: *\nAllow: /\n", "text/plain")
        if url.path == "/_stats":
            with STATS_LOCK:
                return self._send(200, json.dumps(STATS), "application/json")
        if url.path != "/products":
            return self._send(404, "not found", "text/plain")
        try:
            page = int(parse_qs(url.query).get("page", ["1"])[0])
        except ValueError:
            page = 0
        if not 1 <= page <= TOTAL_PAGES:
            return self._send(404, "no such page", "text/plain")

        time.sleep(random.uniform(*LATENCY))
        roll = random.random()
        with STATS_LOCK:
            STATS["requests"] += 1
            key = "http_429" if roll < P_429 else "http_500" if roll < P_429 + P_500 else "http_200"
            STATS[key] += 1
        if key == "http_429":
            return self._send(429, "slow down", "text/plain", {"Retry-After": "1"})
        if key == "http_500":
            return self._send(500, "internal error", "text/plain")
        return self._send(200, render_page(page))


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 256


def main():
    ap = argparse.ArgumentParser(description="Local replica catalog (synthetic data)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    server = Server((args.host, args.port), Handler)
    print(f"Replica catalog on http://{args.host}:{args.port}  "
          f"({TOTAL_PRODUCTS:,} products, {TOTAL_PAGES:,} pages)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
