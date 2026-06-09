-- VTI sector universe and sector-sliced views for >$5B market-cap classified tickers.
-- Source table/view priority: v_vti_ticker_rich for latest market/technical fields, ticker_classification_cache via v_vti_ticker_classification for reusable classification.

CREATE OR REPLACE VIEW v_vti_sector_universe_5b AS
WITH classified AS (
    SELECT
        r.as_of_date,
        r.ticker,
        COALESCE(r.name, c.holding_name, r.holding_name) AS company_name,
        r.holding_name,
        r.holding_value_usd,
        r.holding_pct_value,
        r.market_cap,
        r.exchange,
        r.currency,
        r.close,
        r.volume,
        r.avg_vol_20,
        r.dollar_volume,
        r.dollar_volume_20d_polygon,
        r.price_date,
        c.sector AS raw_sector,
        c.industry,
        c.sub_industry,
        c.gics_sector,
        c.gics_industry_group,
        c.gics_industry,
        c.gics_sub_industry,
        c.source AS classification_source,
        c.confidence AS classification_confidence,
        CASE
            WHEN c.sector IN ('Technology', 'Information Technology') THEN 'Information Technology'
            WHEN c.sector IN ('Healthcare', 'Health Care') THEN 'Health Care'
            WHEN c.sector IN ('Financial Services', 'Financials') THEN 'Financials'
            WHEN c.sector IN ('Consumer Cyclical', 'Consumer Discretionary') THEN 'Consumer Discretionary'
            WHEN c.sector IN ('Consumer Defensive', 'Consumer Staples') THEN 'Consumer Staples'
            WHEN c.sector IN ('Basic Materials', 'Materials') THEN 'Materials'
            WHEN c.sector = 'Communication Services' THEN 'Communication Services'
            WHEN c.sector = 'Industrials' THEN 'Industrials'
            WHEN c.sector = 'Energy' THEN 'Energy'
            WHEN c.sector = 'Utilities' THEN 'Utilities'
            WHEN c.sector = 'Real Estate' THEN 'Real Estate'
            ELSE NULL
        END AS sector,
        r.sic_code,
        r.sic_description,
        r.sma_20,
        r.sma_50,
        r.sma_200,
        r.ema_20,
        r.rsi_14,
        r.atr_14,
        r.calc_rsi_14,
        r.calc_atr_pct,
        r.from_52w_high_pct,
        r.from_52w_low_pct,
        r.price_vs_sma20_pct,
        r.price_vs_sma50_pct,
        r.price_vs_sma200_pct,
        r.volume_vs_20d,
        r.volatility_annual_polygon,
        r.short_interest,
        r.days_to_cover,
        r.short_pct_float,
        r.sentiment_score,
        r.sentiment_articles,
        r.polygon_keyword_count,
        r.primary_keyword_factor,
        r.primary_keyword_factor_score,
        r.keyword_factor_baskets
    FROM v_vti_ticker_rich r
    JOIN v_vti_ticker_classification c USING (ticker)
    WHERE r.market_cap > 5000000000
      AND c.sector IS NOT NULL
      AND c.sector <> ''
      AND c.sector <> 'No Data'
), weighted AS (
    SELECT
        *,
        market_cap / NULLIF(SUM(market_cap) OVER (PARTITION BY sector), 0) AS sector_market_cap_weight,
        market_cap / NULLIF(SUM(market_cap) OVER (), 0) AS universe_market_cap_weight,
        holding_pct_value / NULLIF(SUM(holding_pct_value) OVER (PARTITION BY sector), 0) AS sector_vti_weight,
        holding_pct_value / NULLIF(SUM(holding_pct_value) OVER (), 0) AS universe_vti_weight,
        ROW_NUMBER() OVER (PARTITION BY sector ORDER BY market_cap DESC NULLS LAST, ticker) AS sector_market_cap_rank,
        ROW_NUMBER() OVER (ORDER BY market_cap DESC NULLS LAST, ticker) AS universe_market_cap_rank
    FROM classified
    WHERE sector IS NOT NULL
)
SELECT * FROM weighted;

CREATE OR REPLACE VIEW v_vti_sector_communication_services_5b AS
SELECT * FROM v_vti_sector_universe_5b WHERE sector = 'Communication Services' ORDER BY market_cap DESC NULLS LAST, ticker;

CREATE OR REPLACE VIEW v_vti_sector_consumer_discretionary_5b AS
SELECT * FROM v_vti_sector_universe_5b WHERE sector = 'Consumer Discretionary' ORDER BY market_cap DESC NULLS LAST, ticker;

CREATE OR REPLACE VIEW v_vti_sector_consumer_staples_5b AS
SELECT * FROM v_vti_sector_universe_5b WHERE sector = 'Consumer Staples' ORDER BY market_cap DESC NULLS LAST, ticker;

CREATE OR REPLACE VIEW v_vti_sector_energy_5b AS
SELECT * FROM v_vti_sector_universe_5b WHERE sector = 'Energy' ORDER BY market_cap DESC NULLS LAST, ticker;

CREATE OR REPLACE VIEW v_vti_sector_financials_5b AS
SELECT * FROM v_vti_sector_universe_5b WHERE sector = 'Financials' ORDER BY market_cap DESC NULLS LAST, ticker;

CREATE OR REPLACE VIEW v_vti_sector_health_care_5b AS
SELECT * FROM v_vti_sector_universe_5b WHERE sector = 'Health Care' ORDER BY market_cap DESC NULLS LAST, ticker;

CREATE OR REPLACE VIEW v_vti_sector_industrials_5b AS
SELECT * FROM v_vti_sector_universe_5b WHERE sector = 'Industrials' ORDER BY market_cap DESC NULLS LAST, ticker;

CREATE OR REPLACE VIEW v_vti_sector_information_technology_5b AS
SELECT * FROM v_vti_sector_universe_5b WHERE sector = 'Information Technology' ORDER BY market_cap DESC NULLS LAST, ticker;

CREATE OR REPLACE VIEW v_vti_sector_materials_5b AS
SELECT * FROM v_vti_sector_universe_5b WHERE sector = 'Materials' ORDER BY market_cap DESC NULLS LAST, ticker;

CREATE OR REPLACE VIEW v_vti_sector_real_estate_5b AS
SELECT * FROM v_vti_sector_universe_5b WHERE sector = 'Real Estate' ORDER BY market_cap DESC NULLS LAST, ticker;

CREATE OR REPLACE VIEW v_vti_sector_utilities_5b AS
SELECT * FROM v_vti_sector_universe_5b WHERE sector = 'Utilities' ORDER BY market_cap DESC NULLS LAST, ticker;

CREATE OR REPLACE VIEW v_vti_sector_5b_summary AS
SELECT
    sector,
    COUNT(*) AS ticker_count,
    SUM(market_cap) AS aggregate_market_cap,
    SUM(holding_pct_value) AS aggregate_vti_holding_pct,
    AVG(market_cap) AS avg_market_cap,
    MEDIAN(market_cap) AS median_market_cap,
    SUM(dollar_volume_20d_polygon) AS aggregate_20d_dollar_volume,
    SUM(sector_market_cap_weight * rsi_14) AS cap_weighted_rsi_14,
    SUM(sector_market_cap_weight * calc_rsi_14) AS cap_weighted_calc_rsi_14,
    SUM(sector_market_cap_weight * price_vs_sma20_pct) AS cap_weighted_price_vs_sma20_pct,
    SUM(sector_market_cap_weight * price_vs_sma50_pct) AS cap_weighted_price_vs_sma50_pct,
    SUM(sector_market_cap_weight * price_vs_sma200_pct) AS cap_weighted_price_vs_sma200_pct,
    SUM(sector_market_cap_weight * from_52w_high_pct) AS cap_weighted_from_52w_high_pct,
    SUM(sector_market_cap_weight * from_52w_low_pct) AS cap_weighted_from_52w_low_pct,
    SUM(sector_market_cap_weight * volatility_annual_polygon) AS cap_weighted_volatility_annual,
    SUM(sector_market_cap_weight * short_pct_float) AS cap_weighted_short_pct_float
FROM v_vti_sector_universe_5b
GROUP BY sector
ORDER BY aggregate_market_cap DESC;
