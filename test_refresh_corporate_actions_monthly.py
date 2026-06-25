from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import duckdb

from refresh_corporate_actions_monthly import DateWindow, default_window, fetch_dividends, fetch_splits, merge_staging


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def _paginated_get(self, path, params, max_pages=20):
        self.calls.append((path, params, max_pages))
        return self.responses[path]


class CorporateActionsRefreshTests(unittest.TestCase):
    def test_default_window_looks_back_and_forward(self):
        window = default_window(today=date(2026, 6, 30), lookback_days=45, forward_days=370)
        self.assertEqual(window.start, date(2026, 5, 16))
        self.assertEqual(window.end, date(2027, 7, 5))

    def test_fetch_dividends_sends_polygon_date_filters_and_normalizes_rows(self):
        client = FakeClient({
            "/v3/reference/dividends": {
                "status": "OK",
                "results": [
                    {"ex_dividend_date": "2026-07-01", "cash_amount": 0.25, "declaration_date": "2026-06-01", "pay_date": "2026-07-15", "record_date": "2026-07-02", "frequency": 4, "dividend_type": "CD", "currency": "USD"},
                    {"cash_amount": 1.0},
                ],
            }
        })
        rows = fetch_dividends(client, "aapl", DateWindow(date(2026, 6, 1), date(2027, 6, 30)), limit=1000)
        self.assertEqual(rows, [{
            "_ticker": "aapl",
            "ex_dividend_date": "2026-07-01",
            "cash_amount": 0.25,
            "declaration_date": "2026-06-01",
            "pay_date": "2026-07-15",
            "record_date": "2026-07-02",
            "frequency": 4,
            "dividend_type": "CD",
            "currency": "USD",
        }])
        path, params, max_pages = client.calls[0]
        self.assertEqual(path, "/v3/reference/dividends")
        self.assertEqual(params["ticker"], "aapl")
        self.assertEqual(params["ex_dividend_date.gte"], "2026-06-01")
        self.assertEqual(params["ex_dividend_date.lte"], "2027-06-30")
        self.assertEqual(params["sort"], "ex_dividend_date")
        self.assertEqual(params["order"], "desc")
        self.assertEqual(max_pages, 20)

    def test_fetch_splits_sends_polygon_date_filters_and_normalizes_rows(self):
        client = FakeClient({
            "/v3/reference/splits": {
                "status": "OK",
                "results": [
                    {"execution_date": "2026-06-12", "split_from": 1, "split_to": 10},
                    {"split_from": 1, "split_to": 2},
                ],
            }
        })
        rows = fetch_splits(client, "NVDA", DateWindow(date(2026, 6, 1), date(2027, 6, 30)), limit=100)
        self.assertEqual(rows, [{"_ticker": "NVDA", "execution_date": "2026-06-12", "split_from": 1, "split_to": 10}])
        _, params, _ = client.calls[0]
        self.assertEqual(params["execution_date.gte"], "2026-06-01")
        self.assertEqual(params["execution_date.lte"], "2027-06-30")
        self.assertEqual(params["sort"], "execution_date")

    def test_merge_staging_replaces_existing_keys_transactionally(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            db_path = tmp / "market.duckdb"
            div_path = tmp / "dividends.ndjson"
            split_path = tmp / "splits.ndjson"
            con = duckdb.connect(str(db_path))
            con.execute("""
                CREATE TABLE dividends (
                    ticker VARCHAR NOT NULL,
                    ex_dividend_date DATE NOT NULL,
                    cash_amount DOUBLE,
                    declaration_date DATE,
                    pay_date DATE,
                    record_date DATE,
                    frequency INTEGER,
                    dividend_type VARCHAR,
                    currency VARCHAR,
                    PRIMARY KEY (ticker, ex_dividend_date)
                )
            """)
            con.execute("""
                CREATE TABLE splits (
                    ticker VARCHAR NOT NULL,
                    execution_date DATE NOT NULL,
                    split_from INTEGER,
                    split_to INTEGER,
                    PRIMARY KEY (ticker, execution_date)
                )
            """)
            con.execute("INSERT INTO dividends VALUES ('AAPL', '2026-07-01', 0.20, NULL, NULL, NULL, 4, 'CD', 'USD')")
            con.execute("INSERT INTO splits VALUES ('NVDA', '2026-06-12', 1, 5)")
            con.close()

            div_path.write_text(json.dumps({"_ticker": "AAPL", "ex_dividend_date": "2026-07-01", "cash_amount": 0.25, "declaration_date": "2026-06-01", "pay_date": "2026-07-15", "record_date": "2026-07-02", "frequency": 4, "dividend_type": "CD", "currency": "USD"}) + "\n")
            split_path.write_text(json.dumps({"_ticker": "NVDA", "execution_date": "2026-06-12", "split_from": 1, "split_to": 10}) + "\n")

            metrics = merge_staging(db_path, div_path, split_path)
            self.assertEqual(metrics["dividend_stage_rows"], 1)
            self.assertEqual(metrics["split_stage_rows"], 1)
            self.assertFalse(div_path.exists())
            self.assertFalse(split_path.exists())

            con = duckdb.connect(str(db_path), read_only=True)
            try:
                self.assertEqual(con.execute("SELECT cash_amount FROM dividends WHERE ticker='AAPL'").fetchone()[0], 0.25)
                self.assertEqual(con.execute("SELECT split_to FROM splits WHERE ticker='NVDA'").fetchone()[0], 10)
                self.assertEqual(con.execute("SELECT COUNT(*) FROM dividends").fetchone()[0], 1)
                self.assertEqual(con.execute("SELECT COUNT(*) FROM splits").fetchone()[0], 1)
            finally:
                con.close()


if __name__ == "__main__":
    unittest.main()
