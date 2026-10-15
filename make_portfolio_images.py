"""
make_portfolio_images.py
Builds 3 Upwork-ready portfolio images (4:3, 2000x1500 PNG) plus 16:9 copies (1600x900)
from the REAL results of a demo run: numbers, log lines, HTML and CSV rows all come from
output/ (created by `python run_demo.py`) or, if missing, from sample_output/.

Run (Arch):  python make_portfolio_images.py
Needs:       python-pillow  (sudo pacman -S python-pillow ttf-dejavu)

Every image carries a "Local demo | synthetic data" tag. Keep it: the run is against a
local replica site with synthetic data, not a client project.
"""
import csv
import json
import os
import re
from PIL import Image, ImageDraw, ImageFont

# ============================ CONFIG ============================
BRAND = "Alif"
TAGLINE = "Python Automation Specialist"
STACK = ["Python", "Requests", "BeautifulSoup", "SQLite", "OpenPyXL"]
OUT_DIR = "portfolio_images"
DEMO_TAG = "Local demo  |  synthetic data"
FONT_REGULAR = None   # optional full path to a .ttf
FONT_BOLD = None
FONT_MONO = None
# ================================================================

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 2000, 1500
SS = 2
BG = (11, 16, 32)
PANEL = (20, 28, 52)
PANEL_2 = (30, 41, 72)
LINE = (48, 61, 100)
TEXT = (240, 244, 255)
MUTED = (150, 163, 196)
ACCENT = (34, 211, 166)
AMBER = (251, 191, 36)
RED = (248, 113, 113)
DARK = (8, 12, 24)
MX = 120
CW = W - 2 * MX


# ------------------------------------------------------------------ real data
def data_dir():
    for name in ("output", "sample_output"):
        if os.path.exists(os.path.join(HERE, name, "run_report.json")):
            return os.path.join(HERE, name)
    raise SystemExit("No run data found. Run `python run_demo.py` first.")


def load_data():
    d = data_dir()
    with open(os.path.join(d, "run_report.json"), encoding="utf-8") as f:
        report = json.load(f)
    with open(os.path.join(d, "run.log"), encoding="utf-8") as f:
        log_lines = [ln.rstrip("\n") for ln in f if ln.strip()]
    with open(os.path.join(d, "page1_raw.html"), encoding="utf-8") as f:
        page_html = f.read()
    csv_path = os.path.join(d, "products.csv")
    if not os.path.exists(csv_path):
        csv_path = os.path.join(d, "products_sample.csv")
    if not os.path.exists(csv_path):
        csv_path = os.path.join(HERE, "sample_output", "products_sample.csv")
    with open(csv_path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return report, log_lines, page_html, rows


def duration(seconds):
    s = int(round(seconds))
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


def megabytes(n):
    return f"{n / 1e6:.1f} MB"


# ------------------------------------------------------------------ fonts
def _first(paths):
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None


FONT_PATHS = {
    "regular": _first([FONT_REGULAR, r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf",
                       "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans.ttf",
                       "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                       "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
                       "/usr/share/fonts/noto/NotoSans-Regular.ttf"]),
    "bold": _first([FONT_BOLD, r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
                    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                    "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
                    "/usr/share/fonts/noto/NotoSans-Bold.ttf"]),
    "mono": _first([FONT_MONO, r"C:\Windows\Fonts\consola.ttf", r"C:\Windows\Fonts\cour.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
                    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
                    "/usr/share/fonts/liberation/LiberationMono-Regular.ttf",
                    "/usr/share/fonts/noto/NotoSansMono-Regular.ttf"]),
}
_cache = {}


def font(size, bold=False, mono=False):
    key = "mono" if mono else ("bold" if bold else "regular")
    path = FONT_PATHS[key] or FONT_PATHS["regular"]
    px = int(round(size * SS))
    if (path, px) not in _cache:
        _cache[(path, px)] = ImageFont.truetype(path, px) if path else ImageFont.load_default(size=px)
    return _cache[(path, px)]


# ------------------------------------------------------------------ canvas
class Canvas:
    def __init__(self):
        self.img = Image.new("RGB", (W * SS, H * SS), BG)
        self.d = ImageDraw.Draw(self.img)

    def rect(self, box, fill=None, outline=None, r=0, width=2):
        x0, y0, x1, y1 = [v * SS for v in box]
        self.d.rounded_rectangle([x0, y0, x1, y1], radius=r * SS, fill=fill, outline=outline, width=width * SS)

    def ellipse(self, center, r, fill):
        x, y = center
        self.d.ellipse([(x - r) * SS, (y - r) * SS, (x + r) * SS, (y + r) * SS], fill=fill)

    def line(self, p0, p1, fill, width):
        self.d.line([p0[0] * SS, p0[1] * SS, p1[0] * SS, p1[1] * SS], fill=fill, width=int(width * SS))

    def poly(self, pts, fill):
        self.d.polygon([(x * SS, y * SS) for x, y in pts], fill=fill)

    def text(self, xy, s, size, fill=TEXT, bold=False, mono=False, anchor="la"):
        self.d.text((xy[0] * SS, xy[1] * SS), s, font=font(size, bold, mono), fill=fill, anchor=anchor)

    def tw(self, s, size, bold=False, mono=False):
        return self.d.textlength(s, font=font(size, bold, mono)) / SS

    def fit(self, s, start, max_w, bold=True, mono=False):
        size = start
        while size > 18 and self.tw(s, size, bold, mono) > max_w:
            size -= 2
        return size

    def save(self, name):
        os.makedirs(os.path.join(HERE, OUT_DIR), exist_ok=True)
        full = self.img.resize((W, H), Image.LANCZOS)
        p43 = os.path.join(HERE, OUT_DIR, name + ".png")
        full.save(p43, optimize=True)
        wide = Image.new("RGB", (1600, 900), BG)
        wide.paste(full.resize((1200, 900), Image.LANCZOS), (200, 0))
        p169 = os.path.join(HERE, OUT_DIR, name + "_16x9.png")
        wide.save(p169, optimize=True)
        return p43, p169


def header(c, label):
    c.rect((MX, 88, MX + 200, 96), fill=ACCENT, r=4)
    c.text((MX, 124), label, 34, fill=ACCENT, bold=True)


def footer(c):
    c.rect((MX, 1384, W - MX, 1386), fill=LINE)
    c.rect((MX, 1416, MX + 28, 1444), fill=ACCENT, r=6)
    c.text((MX + 46, 1430), BRAND, 40, bold=True, anchor="lm")
    x = MX + 46 + c.tw(BRAND, 40, True) + 28
    c.text((x, 1430), TAGLINE, 32, fill=MUTED, anchor="lm")
    c.text((W - MX, 1430), DEMO_TAG, 32, fill=AMBER, anchor="rm")


def chips(c, items, x, y, size=40, pad=34, h=76, gap=24, fill=PANEL_2, outline=LINE, color=TEXT):
    cx, cy = x, y
    for s in items:
        w = c.tw(s, size, True) + 2 * pad
        if cx + w > W - MX:
            cx, cy = x, cy + h + gap
        c.rect((cx, cy, cx + w, cy + h), fill=fill, outline=outline, r=h / 2, width=2)
        c.text((cx + w / 2, cy + h / 2), s, size, fill=color, bold=True, anchor="mm")
        cx += w + gap
    return cy + h


def window(c, box, title):
    x0, y0, x1, y1 = box
    c.rect(box, fill=PANEL, r=24)
    c.rect((x0, y0, x1, y0 + 60), fill=PANEL_2, r=24)
    c.rect((x0, y0 + 30, x1, y0 + 60), fill=PANEL_2)
    c.rect(box, outline=LINE, r=24, width=2)
    for i, col in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        c.ellipse((x0 + 34 + i * 32, y0 + 30), 9, col)
    c.text(((x0 + x1) / 2, y0 + 30), title, 28, fill=MUTED, mono=True, anchor="mm")


def arrow(c, x0, x1, y):
    c.line((x0, y), (x1 - 30, y), ACCENT, 14)
    c.poly([(x1 - 54, y - 40), (x1, y), (x1 - 54, y + 40)], ACCENT)


# ------------------------------------------------------------------ image 1: cover
def pick_card(page_html, rows):
    """First real product card from page 1 whose first 8 HTML lines fit the window."""
    known = {r["sku"]: i for i, r in enumerate(rows)}
    marker = '<div class="c_x9 grid-item"'
    for part in page_html.split(marker)[1:]:
        lines = (marker + part).split("\n")[:8]
        sku = re.search(r'data-sku="([^"]+)"', lines[1])
        if sku and sku.group(1) in known and max(len(ln) for ln in lines) <= 40:
            return lines, known[sku.group(1)]
    raise SystemExit("No suitable product card found in page1_raw.html")


def image1(report, page_html, rows):
    c = Canvas()
    header(c, "WEB SCRAPING  |  DATA CLEANING  |  EXPORTS")
    size = min(c.fit("Web Scraping", 150, CW), c.fit("& Data Pipelines", 150, CW))
    c.text((MX, 190), "Web Scraping", size, bold=True)
    c.text((MX, 190 + int(size * 1.07)), "& Data Pipelines", size, fill=ACCENT, bold=True)
    c.text((MX, 540), "Clean data, delivered on schedule.", 58, fill=MUTED)
    stat = (f"Demo run: {report['unique_products']:,} products from {report['pages_done']:,} pages "
            f"in {duration(report['total_runtime_s'])}")
    c.text((MX, 622), stat, 38, fill=TEXT)

    y0, y1 = 710, 1160
    wl, wr = (MX, y0, MX + 700, y1), (W - MX - 700, y0, W - MX, y1)
    window(c, wl, "page1.html")
    card_lines, idx = pick_card(page_html, rows)
    for i, ln in enumerate(card_lines):
        hot = "data-sku" in ln or "p_a" in ln
        c.text((MX + 32, y0 + 92 + i * 42), ln, 26, fill=RED if hot else MUTED, mono=True)

    window(c, wr, "products.csv")
    x = wr[0] + 28
    cols = [("sku", 0), ("price_usd", 200), ("stock", 380), ("rating", 500)]
    c.rect((x - 8, y0 + 76, wr[2] - 20, y0 + 134), fill=ACCENT, r=10)
    for key, off in cols:
        c.text((x + off + 6, y0 + 105), key, 28, fill=DARK, bold=True, mono=True, anchor="lm")
    for i, row in enumerate(rows[idx:idx + 5]):
        ry = y0 + 144 + i * 56
        c.rect((x - 8, ry, wr[2] - 20, ry + 52), fill=PANEL_2 if i % 2 == 0 else PANEL, r=8)
        for key, off in cols:
            c.text((x + off + 6, ry + 26), row[key] or "-", 28, fill=TEXT, mono=True, anchor="lm")
    arrow(c, MX + 700 + 40, W - MX - 700 - 40, (y0 + y1) // 2)
    c.text((MX + 350, y1 + 24), "Raw page HTML", 36, fill=MUTED, anchor="ma")
    c.text((W - MX - 350, y1 + 24), "Clean, structured data", 36, fill=MUTED, anchor="ma")

    chips(c, STACK, MX, 1252)
    footer(c)
    return c.save("1_cover")


# ------------------------------------------------------------------ image 2: proof
def log_excerpt(lines):
    """Pick the story of the run from the real log: start, retry, crash, resume, dedupe, export."""
    def find(pattern):
        for ln in lines:
            if re.search(pattern, ln):
                return re.sub(r"^\d\d:\d\d:\d\d ", "", ln)
        return None

    picks = [find(p) for p in (r"Starting run #1", r"HTTP 429", r"simulated crash", r"Resuming run",
                               r"Duplicates removed", r"Export finished")]
    return ["$ python scraper.py run --config config.json"] + [p for p in picks if p]


def log_line(c, x, y, s, size=34):
    s = s[:84]
    if s.startswith("$"):
        c.text((x, y), s, size, fill=ACCENT, mono=True)
        return
    for tag, col in (("[WARN]", AMBER), ("[ERROR]", RED), ("[INFO]", ACCENT)):
        if s.startswith(tag):
            c.text((x, y), tag, size, fill=col, mono=True)
            c.text((x + c.tw(tag, size, mono=True), y), s[len(tag):], size, fill=TEXT, mono=True)
            return
    c.text((x, y), s, size, fill=TEXT, mono=True)


def image2(report, log_lines):
    c = Canvas()
    header(c, "PRODUCTION-READY SCRAPERS")
    c.text((MX, 190), "Built for Production", 150, bold=True)
    c.text((MX, 380), "A real run, measured. Crash and resume included.", 52, fill=MUTED)

    metrics = [("Rows scraped", f"{report['rows_scraped']:,}"),
               ("Pages fetched", f"{report['page_success_rate_pct']:.0f}%"),
               ("Retries handled", f"{report['retries']:,}"),
               ("Runtime", duration(report["total_runtime_s"]))]
    gap, n = 30, len(metrics)
    cw = (CW - gap * (n - 1)) / n
    inner = cw - 56
    vsize = min(c.fit(v, 100, inner) for _, v in metrics)
    lsize = min(c.fit(k, 36, inner, bold=False) for k, _ in metrics)
    y0, y1 = 500, 760
    for i, (label, value) in enumerate(metrics):
        x0 = MX + i * (cw + gap)
        c.rect((x0, y0, x0 + cw, y1), fill=PANEL, outline=LINE, r=24)
        c.rect((x0 + 28, y0 + 30, x0 + 76, y0 + 38), fill=ACCENT, r=4)
        c.text((x0 + 28, y0 + 150), value, vsize, fill=ACCENT, bold=True, anchor="lm")
        c.text((x0 + 28, y0 + 215), label, lsize, fill=MUTED, anchor="lm")

    window(c, (MX, 820, W - MX, 1330), "run.log  (local test server)")
    for i, ln in enumerate(log_excerpt(log_lines)[:7]):
        log_line(c, MX + 40, 912 + i * 58, ln)
    footer(c)
    return c.save("2_proof")


# ------------------------------------------------------------------ image 3: pipeline
def image3(report):
    c = Canvas()
    header(c, "THE PIPELINE, MEASURED")
    line1 = f"From {report['pages_done']:,} Pages to Clean Data,"
    size = c.fit(line1, 130, CW - 40)
    c.text((MX, 190), line1, size, bold=True)
    c.text((MX, 190 + int(size * 1.15)), "Automatically.", size, fill=ACCENT, bold=True)

    steps = [("Scrape", [f"{report['pages_done']:,} pages", f"{report['rows_scraped']:,} rows"]),
             ("Clean", [f"{report['duplicates_removed']:,} dupes removed", f"{report['rejected_rows']:,} rows rejected"]),
             ("Store", ["SQLite database", "crash-safe"]),
             ("Export", ["CSV, JSON, XLSX", "plus SQLite"])]
    y0, y1 = 560, 890
    cw, gap = 380, (CW - 4 * 380) / 3
    ssize = min(c.fit(s, 32, cw - 72, bold=False) for _, subs in steps for s in subs)
    for i, (title, subs) in enumerate(steps):
        x0 = MX + i * (cw + gap)
        c.rect((x0, y0, x0 + cw, y1), fill=PANEL, outline=LINE, r=28)
        c.ellipse((x0 + 66, y0 + 80), 34, ACCENT)
        c.text((x0 + 66, y0 + 80), str(i + 1), 40, fill=DARK, bold=True, anchor="mm")
        c.text((x0 + 36, y0 + 180), title, 60, bold=True, anchor="lm")
        for j, s in enumerate(subs):
            c.text((x0 + 36, y0 + 245 + j * 42), s, ssize, fill=MUTED, anchor="lm")
        if i < 3:
            ax = x0 + cw
            c.poly([(ax + 18, y0 + 165 - 26), (ax + gap - 18, y0 + 165), (ax + 18, y0 + 165 + 26)], ACCENT)

    sizes = report["exports_bytes"]
    files = [(n, f"{n} {megabytes(sizes[k])}") for n, k in
             (("CSV", "products.csv"), ("JSON", "products.json"), ("XLSX", "products.xlsx"), ("SQLite", "products.db"))
             if k in sizes]
    c.text((MX, 950), "OUTPUT FILES FROM THIS RUN", 32, fill=MUTED, bold=True)
    b = chips(c, [t for _, t in files], MX, 1002, size=38, pad=30, h=72)
    ly = b + 52
    c.text((MX, ly), "EXERCISED IN THIS RUN", 32, fill=MUTED, bold=True)
    chips(c, ["Retries + backoff", "Rate limiting", "Dedupe", "Validation", "Crash resume"], MX, ly + 52,
          size=38, pad=30, h=72, fill=BG, outline=ACCENT, color=ACCENT)
    footer(c)
    return c.save("3_workflow")


if __name__ == "__main__":
    if not FONT_PATHS["regular"]:
        print("WARNING: no TrueType font found. Set FONT_REGULAR / FONT_BOLD / FONT_MONO.")
    report, log_lines, page_html, rows = load_data()
    for paths in (image1(report, page_html, rows), image2(report, log_lines), image3(report)):
        print("saved:", paths[0], "and", paths[1])
