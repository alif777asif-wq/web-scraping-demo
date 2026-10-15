# Catalog scraper: crash-safe scraping pipeline (demo project)

A scraper and data pipeline built and measured against a **local replica** of a messy
e-commerce catalog. The replica serves **synthetic data** with realistic problems injected
(random latency, HTTP 429 / 500 errors, duplicates, broken listings). This is a personal
demo project, not client work, and it does not touch any real website.

## Problem
Pull a large paginated catalog into clean, validated data, survive rate limits and server
errors, recover from a crash without starting over, and deliver CSV / JSON / XLSX / SQLite.

## Approach
- **Fetch**: threaded `requests` session, global rate limit (40 req/s), timeouts, exponential
  backoff with jitter, `Retry-After` support.
- **Checkpointing**: each page's rows and its "done" marker are written to SQLite in one
  transaction, so a killed process loses nothing and a rerun skips finished pages.
- **Clean**: dedupe by SKU, validate and normalize price / stock / rating, keep rejected rows
  with a reason instead of dropping them silently.
- **Deliver**: `products.csv`, `products.json`, `products.xlsx`, `products.db`, plus `run_report.json`.

## Stack
Python, Requests, BeautifulSoup (lxml), SQLite, OpenPyXL. Pillow only for the portfolio images.

## Results (one measured run, `sample_output/run_report.json`)
| Metric | Value |
|---|---|
| Pages fetched | 2,400 / 2,400 (100%) |
| Rows scraped | 120,599 |
| Unique products | 120,000 (599 duplicates removed) |
| Valid / rejected | 119,125 / 875 (228 missing name, 647 missing price) |
| Requests | 2,550, 94.3% succeeded first try |
| Errors handled | 102 x HTTP 429, 34 x HTTP 5xx, 136 retries |
| Crash test | process killed at 1,088 / 2,400 pages, rerun resumed with 1,312 remaining |
| Runtime | 65.3 s fetch + 10.6 s clean/export = 75.9 s total |

These numbers come from one machine against a local server with injected latency and
failures. Real sites behave differently; treat them as a measurement of this pipeline in this
setup, not a promise for any target. Re-run `python run_demo.py` to reproduce (error counts
vary slightly per run because the failures are random).

## Run it
```
pip install -r requirements.txt
python run_demo.py                      # server + scraper + simulated crash + resume
python -m unittest discover -s tests -v # parser and cleaning tests (saved HTML fixture)
python make_portfolio_images.py         # portfolio images from the real run data
```
Arch: `sudo pacman -S python-requests python-beautifulsoup4 python-lxml python-openpyxl python-pillow`

Manual: `python replica_server.py` in one terminal, `python scraper.py run` in another
(`fetch` / `finalize` run the phases separately; Ctrl-C is safe, rerun to resume).

## Output schema (`products`)
`sku, name, price_usd, stock, rating, category, url`. Rejected rows are in the `rejected`
table (`sku, reason, name, price_raw, stock_raw`).

## Files
- `replica_server.py`: local test site (synthetic data, injected failures)
- `scraper.py`: fetch, checkpoint, clean, export
- `run_demo.py`: end-to-end run with crash and resume
- `config.json`: workers, rate limit, timeouts, retries, user agent
- `tests/`: parser and cleaning tests
- `sample_output/`: run report, log, page 1 HTML, 100-row CSV sample
