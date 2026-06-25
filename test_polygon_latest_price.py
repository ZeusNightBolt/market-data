from __future__ import annotations

import unittest

from polygon_client import PolygonClient


class FakePolygonClient(PolygonClient):
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def snapshot(self, ticker: str) -> dict:
        self.calls.append(("snapshot", ticker.upper()))
        return self.responses[ticker.upper()]


class PolygonLatestPriceTests(unittest.TestCase):
    def test_latest_price_prefers_last_trade_over_minute_day_prevday(self):
        client = FakePolygonClient({
            "AAPL": {
                "status": "OK",
                "ticker": {
                    "ticker": "AAPL",
                    "updated": 1782400000000000000,
                    "lastTrade": {"p": 199.12, "t": 1782400000000000000},
                    "min": {"c": 198.5, "t": 1782399900000},
                    "day": {"c": 197.5},
                    "prevDay": {"c": 196.0},
                },
            }
        })

        price = client.latest_price("aapl")

        self.assertEqual(price["ticker"], "AAPL")
        self.assertEqual(price["price"], 199.12)
        self.assertEqual(price["source"], "snapshot.lastTrade.p")
        self.assertEqual(price["timestamp"], 1782400000000000000)
        self.assertEqual(price["status"], "OK")

    def test_latest_price_falls_back_to_snapshot_minute_close(self):
        client = FakePolygonClient({
            "MSFT": {
                "status": "OK",
                "ticker": {
                    "ticker": "MSFT",
                    "min": {"c": 415.22, "t": 1782399900000},
                    "day": {"c": 414.0},
                    "prevDay": {"c": 413.0},
                },
            }
        })

        price = client.latest_price("MSFT")

        self.assertEqual(price["price"], 415.22)
        self.assertEqual(price["source"], "snapshot.min.c")
        self.assertEqual(price["timestamp"], 1782399900000)

    def test_latest_price_bulk_is_per_ticker(self):
        client = FakePolygonClient({
            "AAPL": {"status": "OK", "ticker": {"lastTrade": {"p": 199.12}}},
            "MSFT": {"status": "OK", "ticker": {"prevDay": {"c": 413.0}}},
        })

        prices = client.latest_prices(["aapl", "msft"])

        self.assertEqual(set(prices), {"AAPL", "MSFT"})
        self.assertEqual(prices["AAPL"]["price"], 199.12)
        self.assertEqual(prices["MSFT"]["source"], "snapshot.prevDay.c")


if __name__ == "__main__":
    unittest.main()
