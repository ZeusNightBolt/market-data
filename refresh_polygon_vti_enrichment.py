#!/usr/bin/env python3
"""
Refresh VTI-wide Polygon keyword/sentiment/short-interest enrichment into DuckDB.

Creates/updates:
  - polygon_ticker_enrichment_latest
  - polygon_ticker_enrichment_history
  - polygon_keyword_ticker_map
  - keyword_factor_baskets
  - ticker_keyword_factor_membership

Design:
  - Resume-safe/stale-aware: only pulls missing/stale tickers unless --all.
  - API phase writes staged NDJSON, then one serialized DuckDB merge.
  - Keywords are normalized + blocklist-filtered before persistence.
  - Factor baskets are simple warehouse-native classifications based on repeated
    normalized keywords and lightweight domain rules. This is clustering/classification,
    not a predictive factor model.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

import duckdb
import pandas as pd

sys.path.insert(0, str(Path.home() / "market-data"))
from polygon_client import PolygonClient

DB_PATH = Path.home() / "market-data" / "market_data.duckdb"
RAW_DIR = Path.home() / "market-data" / "raw"
STAGING_PATH = RAW_DIR / "polygon_vti_enrichment.ndjson"
INCOMPLETE_PATH = RAW_DIR / "polygon_vti_enrichment_incomplete.json"

KEYWORD_BLOCKLIST = {
    "securities fraud", "class action", "class action lawsuit", "shareholder lawsuit",
    "shareholder investigation", "shareholder rights", "shareholder class action",
    "merger investigation", "merger and acquisition", "m&a transaction", "m&a",
    "securities investigation", "securities class action", "securities lawsuit",
    "securities litigation", "securities claims", "securities law", "securities law firm",
    "sec violations", "sec investigation", "doj investigation", "doj complaint",
    "lead plaintiff", "lead plaintiff deadline", "investor lawsuit", "investor rights",
    "investor losses", "investor harm", "false statements", "false claims",
    "false claims act", "misleading statements", "misleading disclosures",
    "financial misstatement", "financial restatement", "financial misconduct",
    "financial misrepresentation", "financial reporting", "fiduciary duty breach",
    "fiduciary duty", "unreconciled cash balance", "internal controls",
    "internal controls weakness", "material weaknesses", "illegal kickbacks", "kickbacks",
    "stock price decline", "stock decline", "stock exit", "securities", "lawsuit",
    "investigation", "litigation",
    # P1 #8: Generic financial filler phrases
    "growth stock", "top stock", "best stock", "undervalued", "overvalued",
    "top pick", "buy alert", "stock market", "wall street", "investing",
    "trading", "technical analysis", "breakout", "pullback", "support level",
    "resistance", "bull market", "bear market", "correction", "rally", "sell-off",
    # TASK A: Generic single-word financial terms — too common to signal factor exposure
    "earnings", "news", "acquisition", "dividend", "markets", "merger",
    "movers", "valuation", "buyback", "insider", "ratings", "equity",
    "small cap", "penny stocks", "trading ideas", "shareholder returns",
    "analyst ratings", "analyst upgrades", "analyst downgrades", "price target",
    "financial results", "earnings reports", "earnings release", "quarterly results",
    "quarterly earnings", "quarterly dividend", "intraday update", "pre-market outlook",
    "pre-market", "after hours", "conference call", "market growth", "long ideas",
    "revenue growth", "dividend yield", "dividend stocks", "stock repurchase",
    "share repurchase", "shareholder meeting", "annual meeting", "board of directors",
    "institutional ownership", "insider trading", "insider buying", "insider selling",
    "analyst report", "analyst coverage", "price targets", "recommendation",
    "earnings releases and operating results", "manda",
}

# Hand-built first-pass taxonomy. Baskets are assigned by repeated keywords matching
# these regexes; unclassified repeated keywords fall into emergent keyword baskets.

# P1 #9: High-signal 2-char keywords allowed past the len(k) < 3 filter
KEYWORD_ALLOW_SHORT: set[str] = {"ai", "ev", "5g", "ml", "ar", "vr", "mr", "pc"}

BASKET_RULES: dict[str, list[str]] = {
    "AI Infrastructure": [r"\bai\b", r"artificial intelligence", r"machine learning", r"gpu", r"data center", r"datacenter", r"accelerator", r"llm", r"generative ai", r"agentic"],
    "Semiconductors": [r"semiconductor", r"chip", r"memory", r"dram", r"nand", r"foundry", r"wafer", r"silicon", r"photomask", r"eda", r"fabless"],
    "Cybersecurity": [r"cyber", r"ransomware", r"zero.trust", r"threat", r"security", r"identity", r"firewall", r"endpoint"],
    "Cloud Software": [r"cloud", r"saas", r"software", r"observability", r"database", r"crm", r"enterprise software", r"platform"],
    "Crypto / Digital Assets": [r"crypto", r"bitcoin", r"ethereum", r"blockchain", r"digital asset", r"stablecoin", r"token"],
    "Energy Transition": [r"renewable", r"solar", r"wind", r"battery", r"ev", r"electric vehicle", r"charging", r"hydrogen", r"energy transition"],
    "Oil & Gas": [r"oil", r"natural gas", r"lng", r"shale", r"drilling", r"pipeline", r"refining", r"opec"],
    "Healthcare / Biotech": [r"clinical", r"fda", r"drug", r"therapy", r"biotech", r"pharma", r"medical", r"oncology", r"diabetes", r"glp"],
    "Consumer / Retail": [r"retail", r"consumer", r"restaurant", r"e.commerce", r"grocery", r"same.store", r"apparel", r"travel"],
    "Financials / Credit": [r"bank", r"credit", r"loan", r"deposit", r"insurance", r"asset management", r"fintech", r"payments", r"mortgage"],
    "Defense / Aerospace": [r"defense", r"aerospace", r"missile", r"space", r"satellite", r"army", r"navy", r"air force", r"geopolitics"],
    "Industrial Automation": [r"industrial", r"automation", r"robotics", r"manufacturing", r"supply chain", r"logistics", r"equipment"],
    "Housing / Real Estate": [r"housing", r"real estate", r"reit", r"construction", r"homebuilder", r"mortgage rate", r"property"],
    "Commodities / Materials": [r"copper", r"gold", r"steel", r"aluminum", r"lithium", r"mining", r"chemical", r"materials", r"commodity"],
    "Macro Rates / Inflation": [r"inflation", r"treasury", r"bond yield", r"fed", r"interest rate", r"recession", r"tariff", r"cpi", r"macro"],
}


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def normalize_keyword(kw: str) -> str:
    s = re.sub(r"\s+", " ", str(kw).strip().lower())
    s = s.replace("&", "and")
    s = re.sub(r"[^a-z0-9+./ -]", "", s)
    s = re.sub(r"\s+", " ", s).strip(" -")
    aliases = {
        "data centers": "data center",
        "datacenters": "data center",
        "ai chips": "ai chip",
        "semiconductors": "semiconductor",
        "cyber security": "cybersecurity",
        "electric vehicles": "electric vehicle",
        "interest rates": "interest rate",
        "treasury yields": "treasury yield",
        "oil prices": "oil price",
        "bond yields": "bond yield",
    }
    return aliases.get(s, s)


def keyword_allowed(kw: str) -> bool:
    k = normalize_keyword(kw)
    if not k or (len(k) < 3 and k not in KEYWORD_ALLOW_SHORT):
        return False
    if k in KEYWORD_BLOCKLIST:
        return False
    for phrase in KEYWORD_BLOCKLIST:
        if len(phrase.split()) >= 2 and phrase in k:
            return False
    return True


def rate_wait(lock: Lock, last_call: list[float], rate_limit: float) -> None:
    with lock:
        elapsed = time.time() - last_call[0]
        gap = 1.0 / rate_limit if rate_limit > 0 else 0.0
        if elapsed < gap:
            time.sleep(gap - elapsed)
        last_call[0] = time.time()


def load_candidate_tickers(con: duckdb.DuckDBPyConnection, stale_hours: float, all_tickers: bool, limit: int | None) -> list[str]:
    # Guard against first-run when vti_daily_enriched_latest doesn't exist yet
    try:
        con.execute("SELECT 1 FROM vti_daily_enriched_latest LIMIT 1")
    except Exception:
        log.warning("vti_daily_enriched_latest not found — no VTI universe to enrich against. "
                     "Run build_vti_daily_enriched.py first.")
        return []
    if all_tickers:
        query = "SELECT ticker FROM vti_daily_enriched_latest ORDER BY ticker"
        rows = con.execute(query).fetchall()
    else:
        rows = con.execute("""
            SELECT v.ticker
            FROM vti_daily_enriched_latest v
            LEFT JOIN polygon_ticker_enrichment_latest p USING (ticker)
            WHERE p.ticker IS NULL
               OR p.enriched_at < now() - (? * INTERVAL '1 hour')
            ORDER BY v.ticker
        """, [stale_hours]).fetchall()
    tickers = [r[0] for r in rows]
    if limit:
        tickers = tickers[:limit]
    return tickers


def enrich_one(client: PolygonClient, ticker: str, shares_outstanding: float | None, rate_lock: Lock, last_call: list[float], rate_limit: float, news_limit: int) -> dict[str, Any]:
    row: dict[str, Any] = {"ticker": ticker, "enriched_at": now_utc_iso(), "ok": True, "error": None}
    keywords: list[str] = []
    try:
        rate_wait(rate_lock, last_call, rate_limit)
        news = client.ticker_news(ticker, limit=news_limit, order="desc", sort="published_utc")
        if news.get("status") not in ("OK", "DELAYED"):
            row["ok"] = False
            row["error"] = str(news.get("message") or news.get("status") or "news_not_ok")[:500]
        articles = news.get("results", []) if isinstance(news, dict) else []
        counts = Counter({"positive": 0, "negative": 0, "neutral": 0})
        for a in articles:
            for kw in a.get("keywords", []) or []:
                if keyword_allowed(kw):
                    nk = normalize_keyword(kw)
                    if nk not in keywords:
                        keywords.append(nk)
            for ins in a.get("insights", []) or []:
                if str(ins.get("ticker", "")).upper() == ticker.upper():
                    sent = str(ins.get("sentiment", "neutral")).lower()
                    if sent not in {"positive", "negative", "neutral"}:
                        sent = "neutral"
                    counts[sent] += 1
        total_sent = sum(counts.values())
        row.update({
            "sentiment_positive": int(counts["positive"]),
            "sentiment_negative": int(counts["negative"]),
            "sentiment_neutral": int(counts["neutral"]),
            "sentiment_articles": len(articles),
            "sentiment_score": round((counts["positive"] - counts["negative"]) / total_sent, 4) if total_sent else None,
            "keywords_json": json.dumps(keywords[:25], ensure_ascii=False),
            "keyword_count": len(keywords[:25]),
        })
    except Exception as exc:
        row.update({"ok": False, "error": f"news:{type(exc).__name__}:{exc}"[:500], "keywords_json": "[]", "keyword_count": 0})

    try:
        rate_wait(rate_lock, last_call, rate_limit)
        si = client.short_interest(ticker, limit=1)
        results = si.get("results", []) if isinstance(si, dict) else []
        if results:
            r = results[0]
            short_interest = r.get("short_interest")
            row["short_interest"] = short_interest
            row["days_to_cover"] = r.get("days_to_cover")
            row["short_avg_daily_volume"] = r.get("avg_daily_volume")
            row["short_settlement_date"] = r.get("settlement_date")
            if short_interest is not None and shares_outstanding:
                row["short_pct_float"] = float(short_interest) / float(shares_outstanding) * 100.0
    except Exception as exc:
        # Short-interest gaps should not poison keyword enrichment.
        row["short_error"] = f"short:{type(exc).__name__}:{exc}"[:500]
    return row


def open_staging(path: Path):
    """Open staging file in append mode for incremental writes. Caller must close."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    return path.open("a")


def append_staging_row(f, row: dict[str, Any], lock: Lock) -> None:
    """Thread-safe append of one NDJSON row to the open staging file."""
    line = json.dumps(row, ensure_ascii=False, default=str) + "\n"
    with lock:
        f.write(line)
        f.flush()


def remove_staging(path: Path = STAGING_PATH) -> None:
    """Delete staging after a successful merge so stale rows are not replayed."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def ensure_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS polygon_ticker_enrichment_latest (
            ticker VARCHAR PRIMARY KEY,
            enriched_at TIMESTAMP,
            ok BOOLEAN,
            error VARCHAR,
            keywords_json VARCHAR,
            keyword_count INTEGER,
            sentiment_positive INTEGER,
            sentiment_negative INTEGER,
            sentiment_neutral INTEGER,
            sentiment_articles INTEGER,
            sentiment_score DOUBLE,
            short_interest DOUBLE,
            days_to_cover DOUBLE,
            short_avg_daily_volume DOUBLE,
            short_settlement_date DATE,
            short_pct_float DOUBLE,
            short_error VARCHAR
        )
    """)
    con.execute("CREATE TABLE IF NOT EXISTS polygon_ticker_enrichment_history AS SELECT * FROM polygon_ticker_enrichment_latest LIMIT 0")
    con.execute("""
        CREATE TABLE IF NOT EXISTS polygon_keyword_ticker_map (
            as_of_date DATE,
            ticker VARCHAR,
            keyword VARCHAR,
            enriched_at TIMESTAMP
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS keyword_factor_baskets (
            as_of_date DATE,
            basket_name VARCHAR,
            keyword VARCHAR,
            ticker_count INTEGER,
            keyword_weight DOUBLE
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS ticker_keyword_factor_membership (
            as_of_date DATE,
            ticker VARCHAR,
            basket_name VARCHAR,
            score DOUBLE,
            matched_keywords_json VARCHAR,
            matched_keyword_count INTEGER
        )
    """)


def merge_staging(con: duckdb.DuckDBPyConnection, path: Path) -> int:
    if not path.exists() or path.stat().st_size == 0:
        return 0
    ensure_tables(con)
    try:
        df = pd.read_json(path, lines=True)
    except (ValueError, Exception) as e:
        log.warning(f"Corrupt or unreadable staging file {path}, removing: {e}")
        try:
            path.unlink()
        except Exception:
            pass
        return 0
    if df.empty:
        return 0
    # Ensure all expected columns exist before registering.
    cols = [
        "ticker", "enriched_at", "ok", "error", "keywords_json", "keyword_count",
        "sentiment_positive", "sentiment_negative", "sentiment_neutral", "sentiment_articles",
        "sentiment_score", "short_interest", "days_to_cover", "short_avg_daily_volume",
        "short_settlement_date", "short_pct_float", "short_error",
    ]
    for c in cols:
        if c not in df.columns:
            df[c] = None
    df = df[cols]
    con.register("polygon_stage_df", df)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE polygon_stage AS
        SELECT
            ticker::VARCHAR AS ticker,
            enriched_at::TIMESTAMP AS enriched_at,
            ok::BOOLEAN AS ok,
            error::VARCHAR AS error,
            keywords_json::VARCHAR AS keywords_json,
            keyword_count::INTEGER AS keyword_count,
            sentiment_positive::INTEGER AS sentiment_positive,
            sentiment_negative::INTEGER AS sentiment_negative,
            sentiment_neutral::INTEGER AS sentiment_neutral,
            sentiment_articles::INTEGER AS sentiment_articles,
            sentiment_score::DOUBLE AS sentiment_score,
            short_interest::DOUBLE AS short_interest,
            days_to_cover::DOUBLE AS days_to_cover,
            short_avg_daily_volume::DOUBLE AS short_avg_daily_volume,
            TRY_CAST(short_settlement_date AS DATE) AS short_settlement_date,
            short_pct_float::DOUBLE AS short_pct_float,
            short_error::VARCHAR AS short_error
        FROM polygon_stage_df
    """)
    cols_sql = "ticker, enriched_at, ok, error, keywords_json, keyword_count, sentiment_positive, sentiment_negative, sentiment_neutral, sentiment_articles, sentiment_score, short_interest, days_to_cover, short_avg_daily_volume, short_settlement_date, short_pct_float, short_error"
    con.execute("BEGIN")
    con.execute("""
        CREATE OR REPLACE TEMP TABLE polygon_stage_latest AS
        SELECT * EXCLUDE (rn)
        FROM (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY enriched_at DESC NULLS LAST) AS rn
            FROM polygon_stage
        )
        WHERE rn = 1
    """)
    con.execute("DELETE FROM polygon_ticker_enrichment_latest USING polygon_stage_latest s WHERE polygon_ticker_enrichment_latest.ticker = s.ticker")
    con.execute(f"INSERT INTO polygon_ticker_enrichment_latest ({cols_sql}) SELECT {cols_sql} FROM polygon_stage_latest")
    con.execute(f"INSERT INTO polygon_ticker_enrichment_history ({cols_sql}) SELECT {cols_sql} FROM polygon_stage")
    con.execute("COMMIT")
    return len(df)


def classify_keyword(keyword: str) -> str | None:
    k = normalize_keyword(keyword)
    for basket, patterns in BASKET_RULES.items():
        for pat in patterns:
            if re.search(pat, k):
                return basket
    return None


def rebuild_keyword_tables(con: duckdb.DuckDBPyConnection, min_keyword_df: int = 3, max_emergent: int = 0) -> None:
    """Rebuild keyword map and factor baskets from enrichment data.

    Applies discrimination filters during rebuild:
    - Keywords in KEYWORD_BLOCKLIST are dropped
    - Keywords on >25% of tickers (max_freq_ratio) are universal noise → dropped
    - Singleton keywords (1 ticker) are dropped
    - Emergent 'Keyword: X' baskets disabled by default (max_emergent=0)
    """
    ensure_tables(con)
    as_of = datetime.now(timezone.utc).date().isoformat()
    latest = con.execute("SELECT ticker, enriched_at, keywords_json FROM polygon_ticker_enrichment_latest").fetchdf()
    map_rows: list[dict[str, Any]] = []
    for _, r in latest.iterrows():
        try:
            kws = json.loads(r["keywords_json"] or "[]")
        except Exception:
            kws = []
        seen = set()
        for kw in kws:
            nk = normalize_keyword(kw)
            if not nk or nk in seen:
                continue
            if not keyword_allowed(kw):  # <-- blocklist filter now live during rebuild
                continue
            seen.add(nk)
            map_rows.append({"as_of_date": as_of, "ticker": r["ticker"], "keyword": nk, "enriched_at": r["enriched_at"]})

    con.execute("DELETE FROM polygon_keyword_ticker_map WHERE as_of_date = ?", [as_of])
    if map_rows:
        con.register("keyword_map_df", pd.DataFrame(map_rows))
        con.execute("INSERT INTO polygon_keyword_ticker_map SELECT * FROM keyword_map_df")

    by_kw: dict[str, set[str]] = defaultdict(set)
    by_ticker: dict[str, set[str]] = defaultdict(set)
    for r in map_rows:
        by_kw[r["keyword"]].add(r["ticker"])
        by_ticker[r["ticker"]].add(r["keyword"])

    n_tickers = max(1, len(by_ticker))
    max_freq = int(n_tickers * 0.25)  # Keywords on >25% of tickers = universal noise

    repeated = {}
    for kw, tickers in by_kw.items():
        cnt = len(tickers)
        if cnt >= min_keyword_df and cnt <= max_freq and cnt >= 2:
            repeated[kw] = tickers

    kw_to_baskets: dict[str, set[str]] = defaultdict(set)
    for kw in repeated:
        b = classify_keyword(kw)
        if b:
            kw_to_baskets[kw].add(b)

    # Emergent baskets: only for genuinely uncaptured, high-signal keywords.
    # Disabled by default (max_emergent=0) — raw 'Keyword: X' baskets are noise.
    if max_emergent > 0:
        uncaptured = [(kw, len(tickers)) for kw, tickers in repeated.items() if kw not in kw_to_baskets]
        uncaptured.sort(key=lambda x: (-x[1], x[0]))
        for kw, _ in uncaptured[:max_emergent]:
            kw_to_baskets[kw].add(f"Keyword: {kw}")

    basket_rows = []
    for kw, baskets in kw_to_baskets.items():
        df = len(by_kw[kw])
        weight = math.log((1 + n_tickers) / (1 + df)) + 1.0
        for basket in baskets:
            basket_rows.append({
                "as_of_date": as_of,
                "basket_name": basket,
                "keyword": kw,
                "ticker_count": df,
                "keyword_weight": weight,
            })
    con.execute("DELETE FROM keyword_factor_baskets WHERE as_of_date = ?", [as_of])
    if basket_rows:
        con.register("basket_df", pd.DataFrame(basket_rows))
        con.execute("INSERT INTO keyword_factor_baskets SELECT * FROM basket_df")

    basket_kw = defaultdict(dict)
    for r in basket_rows:
        basket_kw[r["basket_name"]][r["keyword"]] = r["keyword_weight"]

    membership_rows = []
    for ticker, kws in by_ticker.items():
        for basket, weights in basket_kw.items():
            matched = sorted(kws & set(weights.keys()))
            if not matched:
                continue
            score = sum(weights[k] for k in matched)
            membership_rows.append({
                "as_of_date": as_of,
                "ticker": ticker,
                "basket_name": basket,
                "score": score,
                "matched_keywords_json": json.dumps(matched, ensure_ascii=False),
                "matched_keyword_count": len(matched),
            })
    con.execute("DELETE FROM ticker_keyword_factor_membership WHERE as_of_date = ?", [as_of])
    if membership_rows:
        con.register("membership_df", pd.DataFrame(membership_rows))
        con.execute("INSERT INTO ticker_keyword_factor_membership SELECT * FROM membership_df")

    con.execute("""
        CREATE OR REPLACE VIEW v_ticker_keyword_factor_top AS
        WITH ranked AS (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY score DESC, matched_keyword_count DESC, basket_name) AS rn
            FROM ticker_keyword_factor_membership
            WHERE as_of_date = (SELECT MAX(as_of_date) FROM ticker_keyword_factor_membership)
        )
        SELECT * FROM ranked WHERE rn <= 5
    """)
    con.execute("""
        CREATE OR REPLACE VIEW v_keyword_factor_basket_summary AS
        SELECT basket_name,
               COUNT(DISTINCT ticker) AS ticker_count,
               AVG(score) AS avg_score,
               MAX(score) AS max_score,
               string_agg(ticker, ', ' ORDER BY score DESC, ticker) AS tickers
        FROM ticker_keyword_factor_membership
        WHERE as_of_date = (SELECT MAX(as_of_date) FROM ticker_keyword_factor_membership)
        GROUP BY basket_name
        ORDER BY ticker_count DESC, avg_score DESC
    """)


def main() -> None:
    ap = argparse.ArgumentParser(description="Refresh VTI-wide Polygon keyword/sentiment/short enrichment")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--stale-hours", type=float, default=24*7)
    ap.add_argument("--all", action="store_true", help="Refresh all VTI tickers, not just stale/missing")
    ap.add_argument("--limit", type=int, help="Limit tickers for smoke tests")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--rate-limit", type=float, default=20.0)
    ap.add_argument("--news-limit", type=int, default=20)
    ap.add_argument("--insert-only", action="store_true", help="Merge existing staging NDJSON only")
    ap.add_argument("--classify-only", action="store_true", help="Only rebuild keyword maps/baskets from latest table")
    ap.add_argument("--min-keyword-df", type=int, default=3)
    args = ap.parse_args()

    con = duckdb.connect(args.db)
    try:
        ensure_tables(con)
        if args.insert_only:
            n = merge_staging(con, STAGING_PATH)
            log(f"Merged {n:,} staged rows")
            remove_staging(STAGING_PATH)
            rebuild_keyword_tables(con, min_keyword_df=args.min_keyword_df)
            return
        if args.classify_only:
            rebuild_keyword_tables(con, min_keyword_df=args.min_keyword_df)
            log("Rebuilt keyword factor baskets")
            return

        shares = dict(con.execute("SELECT ticker, shares_outstanding FROM vti_daily_enriched_latest").fetchall())
        tickers = load_candidate_tickers(con, args.stale_hours, args.all, args.limit)
        log(f"Tickers to refresh: {len(tickers):,}")
        if not tickers:
            rebuild_keyword_tables(con, min_keyword_df=args.min_keyword_df)
            log("No stale tickers; rebuilt keyword factor baskets")
            return
    finally:
        con.close()

    # Crash recovery: merge any leftover staging before opening a fresh append
    # file. Without this, a successful previous run's NDJSON gets appended to
    # and replayed forever, bloating history and re-staling latest rows.
    if STAGING_PATH.exists() and STAGING_PATH.stat().st_size > 0:
        con = duckdb.connect(args.db)
        try:
            n = merge_staging(con, STAGING_PATH)
            log(f"Merged {n:,} leftover staged rows before refresh")
            remove_staging(STAGING_PATH)
        finally:
            con.close()

    rows_written = 0
    completed: set[str] = set()
    timed_out = False
    rate_lock = Lock()
    write_lock = Lock()
    last_call = [0.0]
    staging_f = open_staging(STAGING_PATH)
    try:
        with PolygonClient() as client:
            pool = ThreadPoolExecutor(max_workers=args.workers)
            futs = {pool.submit(enrich_one, client, t, shares.get(t), rate_lock, last_call, args.rate_limit, args.news_limit): t for t in tickers}
            try:
                for i, fut in enumerate(as_completed(futs, timeout=max(300, len(futs)*20)), 1):
                    t = futs[fut]
                    completed.add(t)
                    try:
                        row = fut.result(timeout=60)
                    except Exception as exc:
                        row = {"ticker": t, "enriched_at": now_utc_iso(), "ok": False, "error": f"future:{type(exc).__name__}:{exc}", "keywords_json": "[]", "keyword_count": 0}
                    append_staging_row(staging_f, row, write_lock)
                    rows_written += 1
                    if i % 50 == 0 or i == len(futs):
                        log(f"Progress: {i:,}/{len(futs):,}")
            except TimeoutError:
                timed_out = True
                remaining = sorted(set(tickers) - completed)
                INCOMPLETE_PATH.write_text(json.dumps({
                    "created_at": now_utc_iso(),
                    "completed": len(completed),
                    "remaining": remaining,
                }, indent=2))
                log(f"as_completed() timeout: collected {len(completed):,}/{len(tickers):,}; {len(remaining):,} remaining written to {INCOMPLETE_PATH}")
                for fut, t in futs.items():
                    if t not in completed:
                        fut.cancel()
            finally:
                # Avoid ThreadPoolExecutor.__exit__ waiting forever on hung urllib calls.
                # Cron's shell timeout is still the hard process kill.
                pool.shutdown(wait=False, cancel_futures=True)
    finally:
        staging_f.close()

    log(f"Wrote staging: {STAGING_PATH} ({rows_written:,} rows)")
    con = duckdb.connect(args.db)
    try:
        n = merge_staging(con, STAGING_PATH)
        log(f"Merged {n:,} rows into polygon_ticker_enrichment_latest/history")
        # Once the staged rows have merged successfully, always remove the
        # staging file.  On timeout, keep only the incomplete manifest; replaying
        # already-merged NDJSON would duplicate history rows on the next run.
        remove_staging(STAGING_PATH)
        if not timed_out:
            remove_staging(INCOMPLETE_PATH)
        rebuild_keyword_tables(con, min_keyword_df=args.min_keyword_df)
        cov = con.execute("""
            SELECT COUNT(*) AS rows,
                   SUM(CASE WHEN keyword_count > 0 THEN 1 ELSE 0 END) AS with_keywords,
                   SUM(CASE WHEN sentiment_articles > 0 THEN 1 ELSE 0 END) AS with_news,
                   SUM(CASE WHEN short_interest IS NOT NULL THEN 1 ELSE 0 END) AS with_short
            FROM polygon_ticker_enrichment_latest
        """).fetchdf()
        print(cov.to_string(index=False))
    finally:
        con.close()


if __name__ == "__main__":
    main()
