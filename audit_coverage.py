#!/usr/bin/env python3
"""Read-only coverage/freshness audit for the market-data warehouse.

Safe for cron/debug use: opens DuckDB read-only, does not call external APIs,
and does not mutate warehouse tables.  In default mode it prints a report and
exits 0.  With --strict it exits non-zero when core daily pipeline invariants
fail, so cron can surface broken refreshes instead of silently succeeding.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

DB = Path("/home/nima/market-data/market_data.duckdb")
SECTOR_MAP = Path.home() / "earnings-reports" / "sector_map_vti.csv"

CORE_TABLES = [
    "daily_bars",
    "hourly_bars",
    "weekly_bars",
    "dividends",
    "splits",
    "ticker_details",
    "technical_indicators",
    "polygon_ticker_enrichment_latest",
    "polygon_keyword_ticker_map",
    "keyword_factor_baskets",
    "ticker_keyword_factor_membership",
    "vti_daily_enriched_latest",
    "vti_daily_enriched_history",
]


def load_vti() -> set[str]:
    if not SECTOR_MAP.exists():
        raise FileNotFoundError(f"Missing VTI sector map: {SECTOR_MAP}")
    tickers: set[str] = set()
    with SECTOR_MAP.open() as f:
        next(f, None)
        for line in f:
            ticker = line.split(",")[0].strip().upper()
            if ticker:
                tickers.add(ticker)
    if not tickers:
        raise RuntimeError(f"No tickers loaded from {SECTOR_MAP}")
    return tickers


def table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    return bool(
        con.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='main' AND table_name=?",
            [table],
        ).fetchone()[0]
    )


def has_column(con: duckdb.DuckDBPyConnection, table: str, column: str) -> bool:
    return bool(
        con.execute(
            "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema='main' AND table_name=? AND column_name=?",
            [table, column],
        ).fetchone()[0]
    )


def print_table_coverage(con: duckdb.DuckDBPyConnection, table: str, vti: set[str]) -> dict[str, object]:
    if not table_exists(con, table):
        print(f"  {table:<36s} MISSING")
        return {"table": table, "exists": False, "rows": 0, "covered": 0, "missing": len(vti), "pct": 0.0}

    cnt = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    if has_column(con, table, "ticker"):
        db_tickers = {r[0] for r in con.execute(f"SELECT DISTINCT ticker FROM {table} WHERE ticker IS NOT NULL").fetchall()}
        covered = len(db_tickers & vti)
        missing = len(vti - db_tickers)
        pct = covered / len(vti) * 100
        print(f"  {table:<36s} {covered:>5,}/{len(vti):<5,} ({pct:5.1f}%) {cnt:>12,} rows {len(db_tickers):>5,} tikrs missing:{missing:>4}")
        return {"table": table, "exists": True, "rows": cnt, "covered": covered, "missing": missing, "pct": pct}

    print(f"  {table:<36s} {'n/a':>17s} {cnt:>12,} rows")
    return {"table": table, "exists": True, "rows": cnt, "covered": None, "missing": None, "pct": None}


def print_latest_dates(con: duckdb.DuckDBPyConnection) -> None:
    print("\nLatest dates:")
    probes = [
        ("daily_bars", "CAST(to_timestamp(MAX(timestamp)/1000) AS DATE)"),
        ("hourly_bars", "CAST(to_timestamp(MAX(timestamp)/1000) AS DATE)"),
        ("weekly_bars", "CAST(to_timestamp(MAX(timestamp)/1000) AS DATE)"),
        ("technical_indicators daily", "CAST(to_timestamp(MAX(timestamp)/1000) AS DATE)", "technical_indicators", "timeframe='daily'"),
        ("vti_daily_enriched_latest", "MAX(as_of_date)"),
        ("polygon_ticker_enrichment_latest", "CAST(MAX(enriched_at) AS DATE)"),
    ]
    for probe in probes:
        label, expr = probe[0], probe[1]
        table = probe[2] if len(probe) > 2 else label
        where = f" WHERE {probe[3]}" if len(probe) > 3 else ""
        if not table_exists(con, table):
            print(f"  {label:<36s} MISSING")
            continue
        try:
            value = con.execute(f"SELECT {expr} FROM {table}{where}").fetchone()[0]
            print(f"  {label:<36s} {value}")
        except Exception as exc:
            print(f"  {label:<36s} ERROR: {exc}")


def print_integrity_checks(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Print non-coverage invariants that catch silent warehouse drift.

    These are deliberately read-only checks: duplicate logical keys, duplicate
    UTC market dates in daily bars, and provisional current-day daily bars. The
    last check matters because the hourly-derived fallback can see pre-market
    bars before a session is complete; those rows must not masquerade as a
    completed daily bar.
    """
    print("\nIntegrity checks:")
    metrics: dict[str, int] = {}
    if table_exists(con, "daily_bars"):
        metrics["daily_duplicate_utc_dates"] = con.execute("""
            SELECT COUNT(*) FROM (
                SELECT ticker, epoch_ms(timestamp::BIGINT)::DATE AS market_date, COUNT(*) AS c
                FROM daily_bars
                GROUP BY 1, 2
                HAVING c > 1
            )
        """).fetchone()[0]
        metrics["daily_current_or_future_provisional_rows"] = con.execute("""
            SELECT COUNT(*)
            FROM daily_bars
            WHERE transactions IS NULL
              AND epoch_ms(timestamp::BIGINT)::DATE >= current_date
        """).fetchone()[0]
        print(f"  daily duplicate UTC market-date groups: {metrics['daily_duplicate_utc_dates']:,}")
        print(f"  daily current/future provisional rows:  {metrics['daily_current_or_future_provisional_rows']:,}")
    for table, key_expr in [
        ("hourly_bars", "ticker, timestamp"),
        ("weekly_bars", "ticker, timestamp"),
        ("technical_indicators", "ticker, timestamp, timeframe"),
    ]:
        if not table_exists(con, table):
            continue
        metric = f"{table}_duplicate_keys"
        metrics[metric] = con.execute(f"""
            SELECT COUNT(*) FROM (
                SELECT {key_expr}, COUNT(*) AS c
                FROM {table}
                GROUP BY {key_expr}
                HAVING c > 1
            )
        """).fetchone()[0]
        print(f"  {table:<28s} duplicate logical keys: {metrics[metric]:,}")
    return metrics


def print_enriched_runs(con: duckdb.DuckDBPyConnection) -> None:
    print("\nvti_daily_enriched_runs latest:")
    if not table_exists(con, "vti_daily_enriched_runs"):
        print("  MISSING")
        return
    cols = [r[0] for r in con.execute("SELECT column_name FROM information_schema.columns WHERE table_name='vti_daily_enriched_runs' ORDER BY ordinal_position").fetchall()]
    wanted = [
        "as_of_date",
        "row_count",
        "with_price",
        "with_ticker_details",
        "with_yfinance_sector",
        "with_upcoming_earnings",
        "with_polygon_keywords",
        "with_calc_indicators",
        "max_price_date",
        "max_recursive_indicator_date",
        "max_calc_indicator_date",
    ]
    select_cols = [c for c in wanted if c in cols]
    df = con.execute(
        f"SELECT {', '.join(select_cols)} FROM vti_daily_enriched_runs ORDER BY as_of_date DESC LIMIT 3"
    ).fetchdf()
    print(df.to_string(index=False))


def table_max_date(con: duckdb.DuckDBPyConnection, table: str, expr: str, where: str = "") -> date | None:
    if not table_exists(con, table):
        return None
    value = con.execute(f"SELECT {expr} FROM {table}{where}").fetchone()[0]
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def strict_issues(
    con: duckdb.DuckDBPyConnection,
    coverage: dict[str, dict[str, object]],
    vti_count: int,
    integrity: dict[str, int] | None = None,
) -> list[str]:
    """Return cron-failing audit issues.

    Thresholds are structural: they catch missing/stale pipeline outputs while
    avoiding false failures from optional fields such as keyword count or
    short-interest availability.
    """
    issues: list[str] = []
    integrity = integrity or {}
    required = [
        "daily_bars",
        "ticker_details",
        "technical_indicators",
        "polygon_ticker_enrichment_latest",
        "vti_daily_enriched_latest",
        "vti_daily_enriched_history",
    ]
    for table in required:
        info = coverage.get(table, {})
        if not info.get("exists"):
            issues.append(f"missing required table: {table}")
            continue
        if int(info.get("rows") or 0) <= 0:
            issues.append(f"empty required table: {table}")

    min_coverage = {
        "daily_bars": 95.0,
        "ticker_details": 95.0,
        "technical_indicators": 95.0,
        "polygon_ticker_enrichment_latest": 90.0,
        "vti_daily_enriched_latest": 95.0,
    }
    for table, threshold in min_coverage.items():
        pct = coverage.get(table, {}).get("pct")
        if pct is not None and float(pct) < threshold:
            issues.append(f"{table} VTI coverage {float(pct):.1f}% < {threshold:.1f}%")

    max_daily = table_max_date(con, "daily_bars", "CAST(to_timestamp(MAX(timestamp)/1000) AS DATE)")
    max_tech = table_max_date(con, "technical_indicators", "CAST(to_timestamp(MAX(timestamp)/1000) AS DATE)", " WHERE timeframe='daily'")
    max_snapshot = table_max_date(con, "vti_daily_enriched_latest", "MAX(as_of_date)")
    today = datetime.now(timezone.utc).date()
    if max_daily and (today - max_daily).days > 7:
        issues.append(f"daily_bars max date stale: {max_daily}")
    if max_daily and max_tech and max_tech < max_daily:
        issues.append(f"technical_indicators daily max {max_tech} < daily_bars max {max_daily}")
    if max_snapshot and (today - max_snapshot).days > 2:
        issues.append(f"vti_daily_enriched_latest as_of_date stale: {max_snapshot}")

    if integrity.get("daily_duplicate_utc_dates", 0) > 0:
        issues.append(f"daily_bars has {integrity['daily_duplicate_utc_dates']:,} duplicate ticker+UTC-date groups")
    if integrity.get("daily_current_or_future_provisional_rows", 0) > 0:
        issues.append(
            f"daily_bars has {integrity['daily_current_or_future_provisional_rows']:,} "
            "current/future provisional hourly-derived rows"
        )
    for metric, value in integrity.items():
        if metric.endswith("_duplicate_keys") and value > 0:
            issues.append(f"{metric.replace('_duplicate_keys', '')} has {value:,} duplicate logical keys")

    if table_exists(con, "vti_daily_enriched_runs"):
        row = con.execute("""
            SELECT row_count, with_price, with_ticker_details, with_calc_indicators, max_price_date, max_calc_indicator_date
            FROM vti_daily_enriched_runs
            ORDER BY as_of_date DESC
            LIMIT 1
        """).fetchone()
        if row:
            row_count, with_price, with_details, with_calc, max_price_date, max_calc_date = row
            if row_count < int(vti_count * 0.95):
                issues.append(f"latest enriched run row_count {row_count:,} < 95% of VTI {vti_count:,}")
            if with_price < int(vti_count * 0.95):
                issues.append(f"latest enriched run with_price {with_price:,} < 95% of VTI {vti_count:,}")
            if with_details < int(vti_count * 0.95):
                issues.append(f"latest enriched run with_ticker_details {with_details:,} < 95% of VTI {vti_count:,}")
            if with_calc < int(vti_count * 0.95):
                issues.append(f"latest enriched run with_calc_indicators {with_calc:,} < 95% of VTI {vti_count:,}")
            if max_price_date and max_calc_date and max_calc_date < max_price_date:
                issues.append(f"latest enriched run max_calc_indicator_date {max_calc_date} < max_price_date {max_price_date}")
    return issues


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit market-data warehouse coverage/freshness")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero if required daily pipeline invariants fail")
    args = parser.parse_args()

    if not DB.exists():
        raise FileNotFoundError(f"Missing DuckDB database: {DB}")
    vti = load_vti()
    con = duckdb.connect(str(DB), read_only=True)
    try:
        print(f"VTI tickers: {len(vti):,}")
        coverage = {}
        for table in CORE_TABLES:
            coverage[table] = print_table_coverage(con, table, vti)
        print_latest_dates(con)
        integrity = print_integrity_checks(con)
        print_enriched_runs(con)
        print(f"\nDB: {DB.stat().st_size / 1024**2:.1f} MB")
        if args.strict:
            issues = strict_issues(con, coverage, len(vti), integrity)
            if issues:
                print("\nSTRICT AUDIT FAILED:")
                for issue in issues:
                    print(f"  - {issue}")
                raise SystemExit(1)
            print("\nSTRICT AUDIT PASSED")
    finally:
        con.close()


if __name__ == "__main__":
    main()
