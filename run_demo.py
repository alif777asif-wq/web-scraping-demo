#!/usr/bin/env python3
"""
End-to-end demo run against the local replica server, including a simulated crash.

  1. start replica_server.py
  2. start the scraper, SIGKILL-style kill it once ~45% of pages are saved
  3. start the scraper again: it resumes from the SQLite checkpoint and finalizes
  4. save server-side request counters + a copy of page 1 for the record

Everything lands in ./output. Run:  python run_demo.py
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "output")
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
CRASH_AT = 0.45  # kill the scraper once this share of pages is checkpointed


def wait_for_server(timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(BASE + "/health", timeout=1).read()
            return
        except OSError:
            time.sleep(0.2)
    raise SystemExit("replica server did not start")


def pages_done():
    try:
        con = sqlite3.connect(os.path.join(OUT, "products.db"), timeout=2)
        n = con.execute("SELECT COUNT(*) FROM pages_done").fetchone()[0]
        con.close()
        return n
    except sqlite3.Error:
        return 0


def log_event(level, message):
    with open(os.path.join(OUT, "run.log"), "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%H:%M:%S} [{level}] {message}\n")


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT)
    sys.path.insert(0, ROOT)
    from replica_server import TOTAL_PAGES

    server = subprocess.Popen([sys.executable, os.path.join(ROOT, "replica_server.py"), "--port", str(PORT)],
                              cwd=ROOT, stdout=subprocess.DEVNULL)
    try:
        wait_for_server()
        with open(os.path.join(OUT, "page1_raw.html"), "wb") as f:
            f.write(urllib.request.urlopen(BASE + "/products?page=1").read())
        cmd = [sys.executable, os.path.join(ROOT, "scraper.py"), "run", "--config", os.path.join(ROOT, "config.json")]

        proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.DEVNULL)
        while proc.poll() is None:
            done = pages_done()
            if done >= CRASH_AT * TOTAL_PAGES:
                proc.kill()
                proc.wait()
                log_event("WARN", f"demo runner: scraper killed at {done:,}/{TOTAL_PAGES:,} pages (simulated crash)")
                break
            time.sleep(0.1)

        result = subprocess.run(cmd, cwd=ROOT, stdout=subprocess.DEVNULL)
        stats = json.loads(urllib.request.urlopen(BASE + "/_stats").read())
        with open(os.path.join(OUT, "server_stats.json"), "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2)
        print("scraper exit code:", result.returncode)
        print("server-side counters:", stats)
        return result.returncode
    finally:
        server.terminate()


if __name__ == "__main__":
    sys.exit(main())
