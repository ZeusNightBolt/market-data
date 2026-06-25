#!/usr/bin/env python3
"""Fetch latest available Polygon snapshot prices for one or more tickers.

Side-effect free: no DuckDB writes and no staging files. Intended for on-demand
checks and final-candidate Equity Screener enrichment.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from polygon_client import PolygonClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch latest Polygon snapshot prices for tickers")
    parser.add_argument("tickers", nargs="*", help="Ticker symbols, e.g. AAPL MSFT")
    parser.add_argument("--file", type=Path, help="Optional newline/comma-separated ticker file")
    parser.add_argument("--timeout", type=int, default=10)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args()


def load_tickers(args: argparse.Namespace) -> list[str]:
    tickers = list(args.tickers)
    if args.file:
        text = args.file.read_text()
        for token in text.replace(",", "\n").splitlines():
            token = token.strip()
            if token:
                tickers.append(token)
    seen: dict[str, None] = {}
    for ticker in tickers:
        symbol = ticker.strip().upper()
        if symbol:
            seen[symbol] = None
    return list(seen)


def main() -> None:
    args = parse_args()
    tickers = load_tickers(args)
    if not tickers:
        raise SystemExit("No tickers supplied")
    client = PolygonClient(timeout=args.timeout, retries=args.retries, max_workers=min(4, len(tickers)))
    payload = {
        "ok": True,
        "count": len(tickers),
        "prices": client.latest_prices(tickers),
    }
    json.dump(payload, sys.stdout, indent=2 if args.pretty else None, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
