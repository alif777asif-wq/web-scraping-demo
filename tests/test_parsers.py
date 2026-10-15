"""Parser and cleaning tests, run against a saved HTML fixture (page 8 of the replica).

Run:  python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scraper  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "page_sample.html")


class ParseTests(unittest.TestCase):
    def setUp(self):
        with open(FIXTURE, encoding="utf-8") as f:
            self.total, self.rows = scraper.parse_page(f.read())

    def test_total_pages_read_from_markup(self):
        self.assertEqual(self.total, 2400)

    def test_page_8_has_50_products_plus_one_sponsored_repeat(self):
        self.assertEqual(len(self.rows), 51)

    def test_ad_slots_are_ignored(self):
        self.assertTrue(all(r["sku"].startswith("SKU-") for r in self.rows))

    def test_first_row_fields(self):
        first = self.rows[0]
        self.assertEqual(first["sku"], "SKU-000351")
        self.assertEqual(first["name"], "Lumen Slim Stand 272")
        self.assertEqual(first["price_raw"], "17.39 USD")
        self.assertEqual(first["url"], "/item/351")

    def test_old_price_is_not_used_as_price(self):
        # old (struck-through) prices live in <s class="p_o">, the live price in span.p_a
        for r in self.rows:
            self.assertNotIn("p_o", r["price_raw"])
            self.assertTrue(r["price_raw"] == "N/A" or any(ch.isdigit() for ch in r["price_raw"]))


class CleanTests(unittest.TestCase):
    def test_price_formats(self):
        self.assertEqual(scraper.clean_price("$1,299.00"), 1299.0)
        self.assertEqual(scraper.clean_price("54.20 USD"), 54.2)

    def test_missing_price_is_rejected(self):
        with self.assertRaises(ValueError):
            scraper.clean_price("N/A")

    def test_stock_variants(self):
        self.assertEqual(scraper.clean_stock("In stock (12)"), 12)
        self.assertEqual(scraper.clean_stock("Only 3 left"), 3)
        self.assertEqual(scraper.clean_stock("Out of stock"), 0)
        with self.assertRaises(ValueError):
            scraper.clean_stock("ships soon")

    def test_rating(self):
        self.assertEqual(scraper.clean_rating("4.3 / 5 (120 reviews)"), 4.3)
        self.assertIsNone(scraper.clean_rating("No ratings yet"))

    def test_validate_rejects_missing_name(self):
        row = (1, 0, "SKU-000001", "", "$5.00", "In stock (1)", "", "Audio", "/item/1")
        with self.assertRaises(ValueError):
            scraper.validate(row, "http://x")


if __name__ == "__main__":
    unittest.main()
