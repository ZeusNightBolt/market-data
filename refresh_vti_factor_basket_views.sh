#!/usr/bin/env bash
set -euo pipefail
cd /home/nima/market-data
exec 9>/tmp/vti_factor_basket_views.lock
flock -n 9 || { echo "Another factor-basket view refresh is running" >&2; exit 75; }

/usr/bin/python3 - <<'PY'
import duckdb
from pathlib import Path

DB = Path('/home/nima/market-data/market_data.duckdb')
SQL_FILES = [
    Path('/home/nima/market-data/sql/create_vti_sector_views.sql'),
    Path('/home/nima/market-data/sql/create_vti_factor_basket_views.sql'),
    Path('/home/nima/market-data/sql/create_vti_factor_basket_production_views.sql'),
    Path('/home/nima/market-data/sql/create_vti_basket_relative_long_ideas_views.sql'),
]

con = duckdb.connect(str(DB))
try:
    for path in SQL_FILES:
        sql = path.read_text()
        con.execute(sql)
        print(f"applied: {path}")

    checks = {
        'sector_universe_rows': "SELECT COUNT(*) FROM v_vti_sector_universe_5b",
        'production_score_rows': "SELECT COUNT(*) FROM v_vti_factor_production_scores_5b",
        'production_membership_rows': "SELECT COUNT(*) FROM v_vti_factor_production_membership_5b",
        'production_vector_rows': "SELECT COUNT(*) FROM v_vti_factor_production_basket_vectors_5b",
        'production_relative_value_rows': "SELECT COUNT(*) FROM v_vti_factor_production_relative_value_5b",
        'relative_long_idea_rows': "SELECT COUNT(*) FROM v_vti_basket_relative_long_ideas_5b",
        'market_cap_filter_violations': "SELECT COUNT(*) FROM v_vti_factor_production_scores_5b WHERE market_cap <= 5000000000 OR market_cap IS NULL",
        'macro_theme_violations': "SELECT COUNT(*) FROM v_vti_factor_production_scores_5b WHERE production_theme ILIKE '%macro%' OR production_theme ILIKE '%inflation%' OR production_theme ILIKE '%labor%'",
    }
    print('\nvalidation:')
    results = {}
    for name, query in checks.items():
        value = con.execute(query).fetchone()[0]
        results[name] = value
        print(f"{name}: {value}")

    if results['sector_universe_rows'] <= 0:
        raise SystemExit('sector universe is empty')
    if results['sector_universe_rows'] != results['production_score_rows']:
        raise SystemExit('production score row count does not match sector universe')
    if results['market_cap_filter_violations'] != 0:
        raise SystemExit('market cap filter violation')
    if results['macro_theme_violations'] != 0:
        raise SystemExit('macro/filler theme leaked into production_theme')
    if results['relative_long_idea_rows'] <= 0:
        raise SystemExit('relative long ideas view is empty')

    con.execute("""
        CREATE TABLE IF NOT EXISTS vti_factor_basket_constituent_snapshots (
            snapshot_date DATE,
            as_of_date DATE,
            ticker VARCHAR,
            company_name VARCHAR,
            sector VARCHAR,
            production_factor_basket VARCHAR,
            production_factor_score DOUBLE,
            production_factor_quintile BIGINT,
            size_bucket VARCHAR,
            liquidity_bucket VARCHAR,
            production_theme VARCHAR,
            dolthub_overlay_label VARCHAR,
            market_cap DOUBLE,
            dollar_volume_20d_polygon DOUBLE,
            ret_1m_pct DOUBLE,
            ret_3m_pct DOUBLE,
            rsi_14 DOUBLE
        )
    """)

    previous_snapshot_date = con.execute("""
        SELECT MAX(snapshot_date)
        FROM vti_factor_basket_constituent_snapshots
        WHERE snapshot_date < CURRENT_DATE
    """).fetchone()[0]

    if previous_snapshot_date is not None:
        print(f"\nchanges_vs_previous_snapshot: {previous_snapshot_date}")
        changes = con.execute("""
            WITH prev AS (
                SELECT *
                FROM vti_factor_basket_constituent_snapshots
                WHERE snapshot_date = ?
            ), curr AS (
                SELECT
                    ticker, company_name, sector, production_factor_basket,
                    production_factor_score, production_factor_quintile, size_bucket,
                    liquidity_bucket, production_theme, dolthub_overlay_label,
                    market_cap, dollar_volume_20d_polygon, ret_1m_pct, ret_3m_pct, rsi_14
                FROM v_vti_factor_production_scores_5b
            ), joined AS (
                SELECT
                    COALESCE(c.ticker, p.ticker) AS ticker,
                    p.production_factor_basket AS old_basket,
                    c.production_factor_basket AS new_basket,
                    p.production_factor_score AS old_score,
                    c.production_factor_score AS new_score,
                    p.market_cap AS old_market_cap,
                    c.market_cap AS new_market_cap,
                    p.size_bucket AS old_size_bucket,
                    c.size_bucket AS new_size_bucket,
                    p.liquidity_bucket AS old_liquidity_bucket,
                    c.liquidity_bucket AS new_liquidity_bucket,
                    p.production_theme AS old_theme,
                    c.production_theme AS new_theme,
                    CASE
                        WHEN p.ticker IS NULL THEN 'NEWLY_QUALIFIED'
                        WHEN c.ticker IS NULL THEN 'DISQUALIFIED_OR_REMOVED'
                        WHEN p.production_factor_basket <> c.production_factor_basket THEN 'BASKET_CHANGED'
                        WHEN p.size_bucket <> c.size_bucket OR p.liquidity_bucket <> c.liquidity_bucket OR COALESCE(p.production_theme,'') <> COALESCE(c.production_theme,'') THEN 'PARAMETER_BUCKET_CHANGED'
                        WHEN ABS(COALESCE(c.production_factor_score,0) - COALESCE(p.production_factor_score,0)) >= 1.0 THEN 'SCORE_MOVED_GE_1PT'
                        ELSE 'UNCHANGED'
                    END AS change_type
                FROM prev p
                FULL OUTER JOIN curr c USING (ticker)
            )
            SELECT change_type, COUNT(*) AS n
            FROM joined
            WHERE change_type <> 'UNCHANGED'
            GROUP BY change_type
            ORDER BY n DESC
        """, [previous_snapshot_date]).fetchdf()
        print(changes.to_string(index=False) if len(changes) else 'no constituent/basket changes detected')
    else:
        print('\nchanges_vs_previous_snapshot: no prior snapshot')

    con.execute("DELETE FROM vti_factor_basket_constituent_snapshots WHERE snapshot_date = CURRENT_DATE")
    con.execute("""
        INSERT INTO vti_factor_basket_constituent_snapshots
        SELECT
            CURRENT_DATE AS snapshot_date,
            as_of_date,
            ticker,
            company_name,
            sector,
            production_factor_basket,
            production_factor_score,
            production_factor_quintile,
            size_bucket,
            liquidity_bucket,
            production_theme,
            dolthub_overlay_label,
            market_cap,
            dollar_volume_20d_polygon,
            ret_1m_pct,
            ret_3m_pct,
            rsi_14
        FROM v_vti_factor_production_scores_5b
    """)
    snapshot_rows = con.execute("SELECT COUNT(*) FROM vti_factor_basket_constituent_snapshots WHERE snapshot_date = CURRENT_DATE").fetchone()[0]
    print(f"snapshot_rows_today: {snapshot_rows}")

    print('\nproduction basket distribution:')
    print(con.execute("""
        SELECT production_factor_basket, COUNT(*) AS n,
               ROUND(AVG(production_factor_score), 2) AS avg_score,
               ROUND(AVG(ret_1m_pct), 2) AS avg_1m,
               ROUND(AVG(ret_3m_pct), 2) AS avg_3m,
               ROUND(AVG(rsi_14), 1) AS avg_rsi
        FROM v_vti_factor_production_scores_5b
        GROUP BY production_factor_basket
        ORDER BY avg_score DESC
    """).fetchdf().to_string(index=False))

    print('\nproduction themes:')
    print(con.execute("""
        SELECT COALESCE(production_theme, 'No Clean Theme') AS theme, COUNT(*) AS n
        FROM v_vti_factor_production_scores_5b
        GROUP BY 1
        ORDER BY n DESC, theme
    """).fetchdf().to_string(index=False))
finally:
    con.close()
PY
