#!/usr/bin/env python3
"""
Crash-safe, polite catalog scraper.

Pipeline:  fetch (threaded, retries, rate limit)  ->  SQLite checkpoint per page
           ->  finalize (dedupe, validate, export CSV / JSON / XLSX / SQLite)

Usage:
    python scraper.py run        # fetch + finalize (default)
    python scraper.py fetch      # fetch only; safe to Ctrl-C or kill, rerun resumes
    python scraper.py finalize   # clean + export what has been fetched
"""
import argparse
import csv
import json
import logging
import os
import random
import re
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

try:
    import lxml  # noqa: F401
    PARSER = "lxml"
except ImportError:  # slower, but no extra dependency
    PARSER = "html.parser"

log = logging.getLogger("scraper")

DEFAULTS = {
    "base_url": "http://127.0.0.1:8765",
    "workers": 16,
    "max_rps": 40,            # global cap on requests per second (politeness)
    "timeout_s": 10,
    "max_retries": 6,
    "backoff_base_s": 0.5,
    "backoff_max_s": 20,
    "user_agent": "portfolio-demo-scraper/1.0",
    "output_dir": "output",
}


# ------------------------------------------------------------------ config / logging
def load_config(args):
    cfg = dict(DEFAULTS)
    if args.config and os.path.exists(args.config):
        with open(args.config, encoding="utf-8") as f:
            cfg.update(json.load(f))
    for key in ("base_url", "workers", "max_rps"):
        value = getattr(args, key, None)
        if value is not None:
            cfg[key] = value
    return cfg


def setup_logging(cfg):
    os.makedirs(cfg["output_dir"], exist_ok=True)
    logging.addLevelName(logging.WARNING, "WARN")
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    for handler in (logging.StreamHandler(sys.stdout),
                    logging.FileHandler(os.path.join(cfg["output_dir"], "run.log"), encoding="utf-8")):
        handler.setFormatter(fmt)
        log.addHandler(handler)


# ------------------------------------------------------------------ database
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS pages_done(page INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS failed_pages(page INTEGER PRIMARY KEY, error TEXT);
CREATE TABLE IF NOT EXISTS raw_products(
    page INTEGER, pos INTEGER, sku TEXT, name TEXT, price_raw TEXT, stock_raw TEXT,
    rating_raw TEXT, category TEXT, url TEXT, PRIMARY KEY(page, pos));
CREATE TABLE IF NOT EXISTS runs(
    run_id INTEGER PRIMARY KEY AUTOINCREMENT, started REAL, updated REAL, ended REAL,
    resumed_from INTEGER DEFAULT 0, pages_fetched INTEGER DEFAULT 0, requests INTEGER DEFAULT 0,
    ok INTEGER DEFAULT 0, r429 INTEGER DEFAULT 0, r5xx INTEGER DEFAULT 0,
    net_err INTEGER DEFAULT 0, retries INTEGER DEFAULT 0, bytes INTEGER DEFAULT 0);
"""


def connect(cfg):
    con = sqlite3.connect(os.path.join(cfg["output_dir"], "products.db"))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    return con


def get_meta(con, key):
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return int(row[0]) if row else None


def set_meta(con, key, value):
    with con:
        con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, str(value)))


# ------------------------------------------------------------------ http layer
class PermanentError(Exception):
    pass


class Stats:
    FIELDS = ("requests", "ok", "r429", "r5xx", "net_err", "retries", "bytes")

    def __init__(self):
        self._lock = threading.Lock()
        self._v = {k: 0 for k in self.FIELDS}

    def add(self, **kw):
        with self._lock:
            for key, n in kw.items():
                self._v[key] += n

    def snap(self):
        with self._lock:
            return dict(self._v)


class RateLimiter:
    """Global requests-per-second cap shared by all worker threads."""

    def __init__(self, rps):
        self.interval = 1.0 / rps if rps else 0.0
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self):
        if not self.interval:
            return
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next)
            self._next = slot + self.interval
        if slot > now:
            time.sleep(slot - now)


def make_session(cfg):
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=cfg["workers"], pool_maxsize=cfg["workers"])
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers["User-Agent"] = cfg["user_agent"]
    return session


def _retry_after(resp):
    try:
        return max(0.0, float(resp.headers.get("Retry-After", "0")))
    except ValueError:
        return 0.0


def fetch_html(session, cfg, limiter, stats, page):
    """GET one page with timeout, exponential backoff + jitter, and Retry-After support."""
    url = cfg["base_url"].rstrip("/") + "/products"
    for attempt in range(cfg["max_retries"] + 1):
        limiter.wait()
        stats.add(requests=1)
        retry_after = 0.0
        try:
            resp = session.get(url, params={"page": page}, timeout=cfg["timeout_s"])
        except (requests.Timeout, requests.ConnectionError) as exc:
            stats.add(net_err=1)
            reason = type(exc).__name__
        else:
            code = resp.status_code
            if code == 200:
                stats.add(ok=1, bytes=len(resp.content))
                return resp.text
            if code == 429:
                stats.add(r429=1)
                retry_after = _retry_after(resp)
                reason = "HTTP 429"
            elif code >= 500:
                stats.add(r5xx=1)
                reason = f"HTTP {code}"
            else:
                raise PermanentError(f"HTTP {code} for page {page}")
        if attempt == cfg["max_retries"]:
            raise PermanentError(f"gave up on page {page} after {attempt + 1} attempts ({reason})")
        delay = max(retry_after, random.uniform(0, min(cfg["backoff_max_s"], cfg["backoff_base_s"] * 2 ** attempt)))
        stats.add(retries=1)
        log.warning("page=%d %s, retry %d/%d in %.1fs", page, reason, attempt + 1, cfg["max_retries"], delay)
        time.sleep(delay)


# ------------------------------------------------------------------ parsing / cleaning
def parse_page(html):
    """Return (total_pages, rows). Rows keep raw strings; cleaning happens later."""
    soup = BeautifulSoup(html, PARSER)
    app = soup.select_one("#app")
    total = int(app["data-total-pages"]) if app is not None and app.has_attr("data-total-pages") else None
    rows = []
    for pos, card in enumerate(soup.select("div.grid-item")):
        def text(selector):
            el = card.select_one(selector)
            return el.get_text(" ", strip=True) if el is not None else ""
        link = card.select_one("a.t_l1")
        rows.append({
            "pos": pos,
            "sku": card.get("data-sku", ""),
            "name": text("span.n"),
            "price_raw": text("span.p_a"),
            "stock_raw": text("div.st_k"),
            "rating_raw": text("div.rt_v"),
            "category": text("span.tag"),
            "url": link["href"] if link is not None and link.has_attr("href") else "",
        })
    return total, rows


_PRICE = re.compile(r"(\d[\d,]*\.?\d*)")


def clean_price(raw):
    if not raw or raw.strip().upper() in {"N/A", "NA", "-"}:
        raise ValueError("missing_price")
    match = _PRICE.search(raw)
    if not match:
        raise ValueError("unparseable_price")
    value = float(match.group(1).replace(",", ""))
    if not 0 < value <= 100_000:
        raise ValueError("price_out_of_range")
    return value


def clean_stock(raw):
    s = raw.lower()
    if "out of stock" in s:
        return 0
    match = re.search(r"(\d+)", s)
    if match and ("in stock" in s or "left" in s):
        return int(match.group(1))
    raise ValueError("unparseable_stock")


def clean_rating(raw):
    match = re.match(r"\s*(\d(?:\.\d)?)\s*/\s*5", raw or "")
    return float(match.group(1)) if match else None


def validate(row, base_url):
    _page, _pos, sku, name, price_raw, stock_raw, rating_raw, category, url = row
    if not sku:
        raise ValueError("missing_sku")
    if not name.strip():
        raise ValueError("missing_name")
    return {
        "sku": sku, "name": name.strip(), "price_usd": clean_price(price_raw),
        "stock": clean_stock(stock_raw), "rating": clean_rating(rating_raw),
        "category": category, "url": urljoin(base_url, url),
    }


# ------------------------------------------------------------------ fetch phase
def save_page(con, run_id, page, rows, stats):
    s = stats.snap()
    with con:  # one transaction: rows + checkpoint marker land together or not at all
        con.executemany(
            "INSERT OR REPLACE INTO raw_products VALUES(?,?,?,?,?,?,?,?,?)",
            [(page, r["pos"], r["sku"], r["name"], r["price_raw"], r["stock_raw"],
              r["rating_raw"], r["category"], r["url"]) for r in rows])
        con.execute("INSERT OR IGNORE INTO pages_done(page) VALUES(?)", (page,))
        con.execute("DELETE FROM failed_pages WHERE page=?", (page,))
        con.execute(
            "UPDATE runs SET updated=?, pages_fetched=pages_fetched+1, requests=?, ok=?, r429=?, "
            "r5xx=?, net_err=?, retries=?, bytes=? WHERE run_id=?",
            (time.time(), s["requests"], s["ok"], s["r429"], s["r5xx"], s["net_err"],
             s["retries"], s["bytes"], run_id))


def run_fetch(cfg, con):
    limiter, stats, session = RateLimiter(cfg["max_rps"]), Stats(), make_session(cfg)
    done = {r[0] for r in con.execute("SELECT page FROM pages_done")}
    now = time.time()
    with con:
        cur = con.execute("INSERT INTO runs(started, updated, resumed_from) VALUES(?,?,?)",
                          (now, now, len(done)))
    run_id = cur.lastrowid
    total = get_meta(con, "total_pages")
    if done and total:
        log.info("Resuming run #%d: %s/%s pages already done, %s remaining",
                 run_id, f"{len(done):,}", f"{total:,}", f"{total - len(done):,}")
    else:
        log.info("Starting run #%d: %s, %d workers, max %s req/s",
                 run_id, cfg["base_url"], cfg["workers"], cfg["max_rps"])
    if total is None:
        total, rows = parse_page(fetch_html(session, cfg, limiter, stats, 1))
        if not total:
            raise SystemExit("could not read total page count from page 1")
        set_meta(con, "total_pages", total)
        save_page(con, run_id, 1, rows, stats)
        done.add(1)
        log.info("Catalog has %s pages", f"{total:,}")

    pending = [p for p in range(1, total + 1) if p not in done]

    def worker(page):
        try:
            _t, rows = parse_page(fetch_html(session, cfg, limiter, stats, page))
            return page, rows, None
        except Exception as exc:  # noqa: BLE001 - reported and checkpointed, never fatal
            return page, None, f"{type(exc).__name__}: {exc}"

    failed, fetched, t0 = 0, 0, time.time()
    pool = ThreadPoolExecutor(max_workers=cfg["workers"])
    try:
        futures = [pool.submit(worker, p) for p in pending]
        for fut in as_completed(futures):
            page, rows, err = fut.result()
            if err:
                failed += 1
                log.error("page=%d failed: %s", page, err)
                with con:
                    con.execute("INSERT OR REPLACE INTO failed_pages VALUES(?,?)", (page, err))
                continue
            save_page(con, run_id, page, rows, stats)
            fetched += 1
            if fetched % 300 == 0:
                n_done = con.execute("SELECT COUNT(*) FROM pages_done").fetchone()[0]
                log.info("Progress %s/%s pages (%.0f pages/s)", f"{n_done:,}", f"{total:,}",
                         fetched / (time.time() - t0))
    except KeyboardInterrupt:
        pool.shutdown(wait=False, cancel_futures=True)
        log.warning("Interrupted: progress is saved, rerun to resume")
        return False
    pool.shutdown(wait=True)
    with con:
        con.execute("UPDATE runs SET ended=?, updated=? WHERE run_id=?", (time.time(), time.time(), run_id))
    n_done = con.execute("SELECT COUNT(*) FROM pages_done").fetchone()[0]
    n_rows = con.execute("SELECT COUNT(*) FROM raw_products").fetchone()[0]
    s = stats.snap()
    log.info("Fetch complete: %s/%s pages, %s rows, %s requests, %s retries, %.1fs",
             f"{n_done:,}", f"{total:,}", f"{n_rows:,}", f"{s['requests']:,}", f"{s['retries']:,}",
             time.time() - t0)
    return failed == 0


# ------------------------------------------------------------------ finalize phase
def write_xlsx(path, rows, columns):
    try:
        from openpyxl import Workbook
    except ImportError:
        log.warning("openpyxl not installed, skipping XLSX export")
        return False
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("products")
    ws.append(columns)
    for r in rows:
        ws.append([r[c] for c in columns])
    wb.save(path)
    return True


def run_finalize(cfg, con):
    t0 = time.time()
    out = cfg["output_dir"]
    raw = con.execute("SELECT page,pos,sku,name,price_raw,stock_raw,rating_raw,category,url "
                      "FROM raw_products ORDER BY page,pos").fetchall()
    seen, unique, duplicates = set(), [], 0
    for row in raw:
        if row[2] in seen:
            duplicates += 1
            continue
        seen.add(row[2])
        unique.append(row)
    log.info("Duplicates removed: %s (of %s rows scraped)", f"{duplicates:,}", f"{len(raw):,}")

    clean, rejected, reasons = [], [], {}
    for row in unique:
        try:
            clean.append(validate(row, cfg["base_url"]))
        except ValueError as exc:
            reason = str(exc)
            reasons[reason] = reasons.get(reason, 0) + 1
            rejected.append((row[2], reason, row[3], row[4], row[5]))
    log.info("Schema validation: %s valid, %s rejected %s", f"{len(clean):,}", f"{len(rejected):,}",
             json.dumps(reasons, sort_keys=True))

    columns = ["sku", "name", "price_usd", "stock", "rating", "category", "url"]
    with con:
        con.executescript("DROP TABLE IF EXISTS products; DROP TABLE IF EXISTS rejected;"
                          "CREATE TABLE products(sku TEXT PRIMARY KEY, name TEXT, price_usd REAL, stock INTEGER,"
                          " rating REAL, category TEXT, url TEXT);"
                          "CREATE TABLE rejected(sku TEXT, reason TEXT, name TEXT, price_raw TEXT, stock_raw TEXT);")
        con.executemany("INSERT INTO products VALUES(?,?,?,?,?,?,?)", [[r[c] for c in columns] for r in clean])
        con.executemany("INSERT INTO rejected VALUES(?,?,?,?,?)", rejected)
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    with open(os.path.join(out, "products.csv"), "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(columns)
        writer.writerows([[r[c] for c in columns] for r in clean])
    with open(os.path.join(out, "products.json"), "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, separators=(",", ":"))
    write_xlsx(os.path.join(out, "products.xlsx"), clean, columns)
    log.info("Export finished: products.csv, products.json, products.xlsx, products.db")

    runs = con.execute("SELECT started, updated, ended, resumed_from, pages_fetched, requests, ok, r429, r5xx, "
                       "net_err, retries, bytes FROM runs ORDER BY run_id").fetchall()
    fetch_s = sum((r[2] or r[1]) - r[0] for r in runs)
    total_pages = get_meta(con, "total_pages") or 0
    pages_done = con.execute("SELECT COUNT(*) FROM pages_done").fetchone()[0]
    requests_total = sum(r[5] for r in runs)
    finalize_s = time.time() - t0
    report = {
        "environment": "local replica server, synthetic data, injected latency / 429 / 500",
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pages_total": total_pages, "pages_done": pages_done,
        "page_success_rate_pct": round(100 * pages_done / total_pages, 2) if total_pages else 0,
        "rows_scraped": len(raw), "unique_products": len(unique), "duplicates_removed": duplicates,
        "valid_rows": len(clean), "rejected_rows": len(rejected), "rejected_by_reason": reasons,
        "requests_total": requests_total, "requests_ok": sum(r[6] for r in runs),
        "request_success_rate_pct": round(100 * sum(r[6] for r in runs) / requests_total, 2) if requests_total else 0,
        "http_429": sum(r[7] for r in runs), "http_5xx": sum(r[8] for r in runs),
        "network_errors": sum(r[9] for r in runs), "retries": sum(r[10] for r in runs),
        "runs": len(runs), "crash_resumes": sum(1 for r in runs if r[3] > 0),
        "fetch_runtime_s": round(fetch_s, 1), "finalize_runtime_s": round(finalize_s, 1),
        "total_runtime_s": round(fetch_s + finalize_s, 1),
        "rows_per_second": round(len(raw) / fetch_s, 1) if fetch_s else 0,
        "exports_bytes": {n: os.path.getsize(os.path.join(out, n))
                          for n in ("products.csv", "products.json", "products.xlsx", "products.db")
                          if os.path.exists(os.path.join(out, n))},
    }
    with open(os.path.join(out, "run_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    log.info("Run report written: %s", os.path.join(out, "run_report.json"))
    return True


# ------------------------------------------------------------------ cli
def main(argv=None):
    ap = argparse.ArgumentParser(description="Crash-safe catalog scraper")
    ap.add_argument("command", nargs="?", default="run", choices=["run", "fetch", "finalize"])
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--base-url", dest="base_url")
    ap.add_argument("--workers", type=int)
    ap.add_argument("--max-rps", dest="max_rps", type=float)
    args = ap.parse_args(argv)
    cfg = load_config(args)
    setup_logging(cfg)
    con = connect(cfg)
    ok = True
    if args.command in ("run", "fetch"):
        ok = run_fetch(cfg, con)
    if ok and args.command in ("run", "finalize"):
        run_finalize(cfg, con)
    elif not ok:
        log.error("Fetch incomplete, not finalizing. Rerun to resume.")
    con.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
