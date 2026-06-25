#!/usr/bin/env python3
"""Monthly incremental refresh for Polygon dividends and stock splits.

This is intentionally separate from the daily OHLCV/indicator refresh. It pulls
recent and forward-dated corporate actions for the VTI universe, stages results
as NDJSON, then transactionally replaces matching keys in DuckDB.
"""
from __future__ import annotations

import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import duckdb

from polygon_client import PolygonClient

ROOT = Path(__file__).resolve().parent
DB = ROOT / "market_data.duckdb"
RAW_DIR = ROOT / "raw" / "corporate_actions"
SECTOR_MAP = Path.home() / "earnings-reports" / "sector_map_vti.csv"
LOCK = threading.Lock()


@dataclass(frozen=True)
class DateWindow:
    start: date
    end: date

    def as_params(self, date_field: str) -> dict[str, str]:
        return {
            f"{date_field}.gte": self.start.isoformat(),
            f"{date_field}.lte": self.end.isoformat(),
        }


def default_window(today: date | None = None, lookback_days: int = 45, forward_days: int = 370) -> DateWindow:
    """Return the default monthly refresh window.

    The lookback catches late corrections for the prior month; the forward range
    catches newly-announced future ex-dividend/split dates, which are common for
    dividends and would be missed by a pure month-to-date filter.
    """
    today = today or date.today()
    return DateWindow(start=today - timedelta(days=lookback_days), end=today + timedelta(days=forward_days))


def load_vti_tickers(path: Path = SECTOR_MAP) -> list[str]:
    if not path.exists():
        raise FileNotFoundError(f"Missing VTI sector map: {path}")
    tickers: set[str] = set()
    with path.open() as f:
        next(f, None)
        for line in f:
            ticker = line.split(",", 1)[0].strip().upper()
            if ticker:
                tickers.add(ticker)
    if not tickers:
        raise RuntimeError(f"No tickers loaded from {path}")
    return sorted(tickers)


def staging_paths(window: DateWindow) -> tuple[Path, Path]:
    suffix = f"{window.start.isoformat()}_{window.end.isoformat()}"
    return RAW_DIR / f"dividends_{suffix}.ndjson", RAW_DIR / f"splits_{suffix}.ndjson"


def _ok(resp: dict) -> bool:
    return isinstance(resp, dict) and resp.get("status") in {"OK", "DELAYED"}


def fetch_dividends(client: PolygonClient, ticker: str, window: DateWindow, limit: int) -> list[dict]:
    params = {
        "ticker": ticker,
        "limit": limit,
        "sort": "ex_dividend_date",
        "order": "desc",
        **window.as_params("ex_dividend_date"),
    }
    resp = client._paginated_get("/v3/reference/dividends", params, max_pages=20)
    if not _ok(resp):
        raise RuntimeError(f"dividends {ticker}: {resp.get('status') or resp.get('error') or resp}")
    rows = []
    for row in resp.get("results", []):
        ex_date = row.get("ex_dividend_date")
        if not ex_date:
            continue
        rows.append({
            "_ticker": ticker,
            "ex_dividend_date": ex_date,
            "cash_amount": row.get("cash_amount"),
            "declaration_date": row.get("declaration_date"),
            "pay_date": row.get("pay_date"),
            "record_date": row.get("record_date"),
            "frequency": row.get("frequency"),
            "dividend_type": row.get("dividend_type"),
            "currency": row.get("currency"),
        })
    return rows


def fetch_splits(client: PolygonClient, ticker: str, window: DateWindow, limit: int) -> list[dict]:
    params = {
        "ticker": ticker,
        "limit": limit,
        "sort": "execution_date",
        "order": "desc",
        **window.as_params("execution_date"),
    }
    resp = client._paginated_get("/v3/reference/splits", params, max_pages=20)
    if not _ok(resp):
        raise RuntimeError(f"splits {ticker}: {resp.get('status') or resp.get('error') or resp}")
    rows = []
    for row in resp.get("results", []):
        execution_date = row.get("execution_date")
        if not execution_date:
            continue
        rows.append({
            "_ticker": ticker,
            "execution_date": execution_date,
            "split_from": row.get("split_from"),
            "split_to": row.get("split_to"),
        })
    return rows


def write_rows(path: Path, rows: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with LOCK:
        with path.open("a") as f:
            for row in rows:
                f.write(json.dumps(row, separators=(",", ":")) + "\n")
                count += 1
    return count


def pull_to_staging(tickers: list[str], window: DateWindow, div_path: Path, split_path: Path,
                    workers: int, dividend_limit: int, split_limit: int, dry_run: bool) -> dict[str, int]:
    stats = {"tickers": len(tickers), "dividend_rows": 0, "split_rows": 0, "failed_tickers": 0}
    if not dry_run:
        div_path.parent.mkdir(parents=True, exist_ok=True)
        div_path.unlink(missing_ok=True)
        split_path.unlink(missing_ok=True)

    def one(ticker: str) -> tuple[str, list[dict], list[dict], str | None]:
        try:
            client = PolygonClient(timeout=20, retries=3, max_workers=workers)
            dividends = fetch_dividends(client, ticker, window, dividend_limit)
            splits = fetch_splits(client, ticker, window, split_limit)
            return ticker, dividends, splits, None
        except Exception as exc:  # per-ticker failure should not kill the whole month
            return ticker, [], [], str(exc)[:300]

    t0 = time.time()
    failures: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, ticker) for ticker in tickers]
        try:
            for fut in as_completed(futures, timeout=max(300, len(tickers) * 30)):
                ticker, dividends, splits, error = fut.result(timeout=60)
                if error:
                    failures.append((ticker, error))
                    stats["failed_tickers"] += 1
                    continue
                stats["dividend_rows"] += len(dividends)
                stats["split_rows"] += len(splits)
                if not dry_run:
                    write_rows(div_path, dividends)
                    write_rows(split_path, splits)
        except TimeoutError:
            for fut in futures:
                fut.cancel()
            raise RuntimeError("corporate-action pull timed out before all tickers completed")

    elapsed = time.time() - t0
    print(f"Pulled {stats['tickers']:,} tickers in {elapsed:.1f}s: dividends={stats['dividend_rows']:,}, splits={stats['split_rows']:,}, failures={stats['failed_tickers']:,}")
    if failures:
        print("First failures:")
        for ticker, error in failures[:10]:
            print(f"  {ticker}: {error}")
    return stats


def merge_staging(db_path: Path, div_path: Path, split_path: Path, keep_staging: bool = False) -> dict[str, int]:
    metrics = {"dividend_stage_rows": 0, "split_stage_rows": 0, "dividends_before": 0, "dividends_after": 0, "splits_before": 0, "splits_after": 0}
    con = duckdb.connect(str(db_path))
    try:
        con.execute("BEGIN")
        metrics["dividends_before"] = con.execute("SELECT COUNT(*) FROM dividends").fetchone()[0]
        metrics["splits_before"] = con.execute("SELECT COUNT(*) FROM splits").fetchone()[0]

        if div_path.exists() and div_path.stat().st_size > 0:
            con.execute(f"""
                CREATE OR REPLACE TEMP TABLE stage_dividends AS
                SELECT DISTINCT
                    _ticker::VARCHAR AS ticker,
                    ex_dividend_date::DATE AS ex_dividend_date,
                    cash_amount::DOUBLE AS cash_amount,
                    declaration_date::DATE AS declaration_date,
                    pay_date::DATE AS pay_date,
                    record_date::DATE AS record_date,
                    frequency::INTEGER AS frequency,
                    dividend_type::VARCHAR AS dividend_type,
                    currency::VARCHAR AS currency
                FROM read_json_auto('{div_path}', format='newline_delimited', ignore_errors=true)
                WHERE _ticker IS NOT NULL AND ex_dividend_date IS NOT NULL
            """)
            metrics["dividend_stage_rows"] = con.execute("SELECT COUNT(*) FROM stage_dividends").fetchone()[0]
            con.execute("""
                DELETE FROM dividends d
                USING stage_dividends s
                WHERE d.ticker = s.ticker AND d.ex_dividend_date = s.ex_dividend_date
            """)
            con.execute("""
                INSERT INTO dividends (ticker, ex_dividend_date, cash_amount, declaration_date, pay_date, record_date, frequency, dividend_type, currency)
                SELECT ticker, ex_dividend_date, cash_amount, declaration_date, pay_date, record_date, frequency, dividend_type, currency
                FROM stage_dividends
            """)

        if split_path.exists() and split_path.stat().st_size > 0:
            con.execute(f"""
                CREATE OR REPLACE TEMP TABLE stage_splits AS
                SELECT DISTINCT
                    _ticker::VARCHAR AS ticker,
                    execution_date::DATE AS execution_date,
                    split_from::INTEGER AS split_from,
                    split_to::INTEGER AS split_to
                FROM read_json_auto('{split_path}', format='newline_delimited', ignore_errors=true)
                WHERE _ticker IS NOT NULL AND execution_date IS NOT NULL
            """)
            metrics["split_stage_rows"] = con.execute("SELECT COUNT(*) FROM stage_splits").fetchone()[0]
            con.execute("""
                DELETE FROM splits s
                USING stage_splits st
                WHERE s.ticker = st.ticker AND s.execution_date = st.execution_date
            """)
            con.execute("""
                INSERT INTO splits (ticker, execution_date, split_from, split_to)
                SELECT ticker, execution_date, split_from, split_to
                FROM stage_splits
            """)

        metrics["dividends_after"] = con.execute("SELECT COUNT(*) FROM dividends").fetchone()[0]
        metrics["splits_after"] = con.execute("SELECT COUNT(*) FROM splits").fetchone()[0]
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()

    if not keep_staging:
        div_path.unlink(missing_ok=True)
        split_path.unlink(missing_ok=True)
    return metrics


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh recent/future Polygon dividends and splits into DuckDB")
    parser.add_argument("--db", type=Path, default=DB)
    parser.add_argument("--start-date", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date())
    parser.add_argument("--end-date", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date())
    parser.add_argument("--lookback-days", type=int, default=45)
    parser.add_argument("--forward-days", type=int, default=370)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--tickers", nargs="*", help="Optional ticker subset for smoke tests")
    parser.add_argument("--dividend-limit", type=int, default=1000)
    parser.add_argument("--split-limit", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true", help="Pull and print counts, but do not stage or merge")
    parser.add_argument("--insert-only", action="store_true", help="Merge existing staging files for the selected date window")
    parser.add_argument("--keep-staging", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.start_date or args.end_date:
        if not (args.start_date and args.end_date):
            raise SystemExit("--start-date and --end-date must be supplied together")
        window = DateWindow(args.start_date, args.end_date)
    else:
        window = default_window(lookback_days=args.lookback_days, forward_days=args.forward_days)
    if window.start > window.end:
        raise SystemExit(f"Invalid date window: {window.start} > {window.end}")

    div_path, split_path = staging_paths(window)
    print("# Monthly corporate actions refresh")
    print(f"window={window.start.isoformat()}..{window.end.isoformat()}")
    print(f"db={args.db}")

    if not args.insert_only:
        tickers = [t.upper() for t in args.tickers] if args.tickers else load_vti_tickers()
        pull_to_staging(tickers, window, div_path, split_path, args.workers, args.dividend_limit, args.split_limit, args.dry_run)
        if args.dry_run:
            print("dry_run=true; skipped DuckDB merge")
            return

    metrics = merge_staging(args.db, div_path, split_path, keep_staging=args.keep_staging)
    for key, value in metrics.items():
        print(f"{key}={value:,}")
    print("✅ Corporate actions refresh complete")


if __name__ == "__main__":
    main()
