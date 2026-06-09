-- Production factor basket layer for VTI >$5B classified universe.
-- Depends on:
--   1. v_vti_sector_universe_5b
--   2. v_vti_factor_features_5b
-- DoltHub V/G/M stays sparse overlay only; core classification is technical + size/liquidity + clean keyword theme.

CREATE OR REPLACE VIEW v_vti_factor_production_scores_5b AS
WITH base AS (
    SELECT
        f.*,
        CASE
            WHEN market_cap >= 1000000000000 THEN 'Mega Cap >$1T'
            WHEN market_cap >= 250000000000 THEN 'Mega Cap $250B-$1T'
            WHEN market_cap >= 50000000000 THEN 'Large Cap $50B-$250B'
            WHEN market_cap >= 10000000000 THEN 'Upper Mid / Large $10B-$50B'
            ELSE 'Lower Liquid $5B-$10B'
        END AS size_bucket,
        CASE
            WHEN liquidity_pctile >= 0.90 THEN 'Top Decile Liquidity'
            WHEN liquidity_pctile >= 0.75 THEN 'Top Quartile Liquidity'
            WHEN liquidity_pctile >= 0.50 THEN 'Above-Median Liquidity'
            WHEN liquidity_pctile >= 0.25 THEN 'Below-Median Liquidity'
            ELSE 'Low Relative Liquidity'
        END AS liquidity_bucket,
        CASE
            WHEN ret_1m_pct >= 20 AND ret_3m_pct >= 30 THEN 'Explosive 1M+3M Momentum'
            WHEN ret_1m_pct >= 10 AND ret_3m_pct >= 15 THEN 'Strong Positive Momentum'
            WHEN ret_1m_pct >= 0 AND ret_3m_pct >= 0 THEN 'Positive / Grinding Higher'
            WHEN ret_1m_pct < -10 AND ret_3m_pct < -15 THEN 'Sharp Negative Momentum'
            WHEN ret_1m_pct < 0 AND ret_3m_pct < 0 THEN 'Negative / Drifting Lower'
            ELSE 'Mixed Momentum'
        END AS return_regime,
        CASE
            WHEN thematic_basket IN ('No Clean Keyword Theme', 'Macro Rates / Inflation') THEN NULL
            ELSE thematic_basket
        END AS production_theme,
        CASE
            WHEN has_dolthub_vgm AND COALESCE(dolt_growth_score, 0) >= 7.5 AND COALESCE(dolt_momentum_score, 0) >= 7.5 THEN 'Sparse DoltHub: Growth+Momentum Confirmed'
            WHEN has_dolthub_vgm AND COALESCE(dolt_value_score, 0) >= 7.5 AND price_momentum_score >= 6.0 THEN 'Sparse DoltHub: Value Catch-Up Confirmed'
            WHEN has_dolthub_vgm AND COALESCE(dolt_value_score, 0) >= 7.5 THEN 'Sparse DoltHub: Value Only'
            WHEN has_dolthub_vgm THEN 'Sparse DoltHub: Mixed/Weak'
            ELSE 'No DoltHub Overlay'
        END AS dolthub_overlay_label,
        CASE
            WHEN has_dolthub_vgm THEN (
                COALESCE(dolt_value_score, 5.0) + COALESCE(dolt_growth_score, 5.0) + COALESCE(dolt_momentum_score, 5.0)
            ) / 3.0
            ELSE NULL
        END AS dolthub_overlay_score
    FROM v_vti_factor_features_5b f
), scored AS (
    SELECT
        *,
        10.0 * (
            0.40 * COALESCE(ret_1m_pctile, 0.5) +
            0.35 * COALESCE(ret_3m_pctile, 0.5) +
            0.15 * COALESCE(ret_6m_pctile, 0.5) +
            0.10 * COALESCE(ret_ytd_pctile, 0.5)
        ) AS production_price_momentum_score,
        10.0 * (
            0.45 * COALESCE(ret_1m_pctile, 0.5) +
            0.30 * COALESCE(ret_3m_pctile, 0.5) +
            0.15 * COALESCE(liquidity_pctile, 0.5) +
            0.10 * COALESCE(universe_market_cap_weight / NULLIF(MAX(universe_market_cap_weight) OVER (), 0), 0.0)
        ) AS size_liquidity_momentum_score,
        10.0 * (
            0.35 * COALESCE(ret_1m_pctile, 0.5) +
            0.20 * COALESCE(ret_3m_pctile, 0.5) +
            0.25 * COALESCE(liquidity_pctile, 0.5) +
            0.20 * CASE
                WHEN market_cap >= 250000000000 THEN 1.0
                WHEN market_cap >= 50000000000 THEN 0.8
                WHEN market_cap >= 10000000000 THEN 0.6
                ELSE 0.4
            END
        ) AS institutional_momentum_score,
        LEAST(10.0, GREATEST(0.0,
            0.70 * COALESCE(primary_keyword_factor_score, 0.0) +
            CASE WHEN production_theme IS NOT NULL THEN 2.0 ELSE 0.0 END
        )) AS clean_theme_score,
        CASE
            WHEN close > sma_20 AND close > sma_50 AND close > sma_200 AND sma_20 > sma_50 THEN 1 ELSE 0
        END AS aligned_uptrend_flag,
        CASE
            WHEN close > keltner_upper THEN 1
            WHEN close < keltner_lower THEN -1
            ELSE 0
        END AS keltner_position_flag
    FROM base
), final AS (
    SELECT
        *,
        (
            0.26 * production_price_momentum_score +
            0.20 * trend_structure_score +
            0.16 * size_liquidity_momentum_score +
            0.12 * institutional_momentum_score +
            0.10 * rsi_regime_score +
            0.08 * vol_risk_score +
            0.05 * liquidity_score +
            0.03 * clean_theme_score
        ) AS core_factor_score,
        CASE
            WHEN dolthub_overlay_score IS NULL THEN 0.0
            WHEN dolthub_overlay_score >= 7.5 THEN 0.25
            WHEN dolthub_overlay_score <= 2.5 THEN -0.25
            ELSE 0.0
        END AS dolthub_sparse_adjustment
    FROM scored
)
SELECT
    *,
    core_factor_score + dolthub_sparse_adjustment AS production_factor_score,
    NTILE(5) OVER (ORDER BY core_factor_score + dolthub_sparse_adjustment DESC NULLS LAST) AS production_factor_quintile,
    CASE
        WHEN market_cap >= 250000000000 AND liquidity_pctile >= 0.70 AND production_price_momentum_score >= 7.0 AND trend_structure_score >= 7.0 THEN 'Mega-Cap Liquid Momentum Leadership'
        WHEN market_cap >= 50000000000 AND liquidity_pctile >= 0.65 AND size_liquidity_momentum_score >= 7.0 AND aligned_uptrend_flag = 1 THEN 'Institutional Large-Cap Accumulation'
        WHEN market_cap < 50000000000 AND production_price_momentum_score >= 8.0 AND volatility_annual_polygon >= 0.45 THEN 'Smaller-Cap High-Beta Breakout'
        WHEN production_theme IS NOT NULL AND production_price_momentum_score >= 7.0 AND trend_structure_score >= 6.0 THEN 'Thematic Momentum Leadership'
        WHEN aligned_uptrend_flag = 1 AND vol_risk_score >= 7.0 THEN 'Quality / Low-Vol Uptrend'
        WHEN rsi_14 > 80 OR (keltner_position_flag = 1 AND ret_1m_pctile >= 0.90) THEN 'Extended Momentum Risk'
        WHEN rsi_14 < 35 OR keltner_position_flag = -1 THEN 'Oversold / Mean-Reversion Watch'
        WHEN close < sma_50 AND close < sma_200 AND ret_3m_pctile <= 0.30 THEN 'Broken Momentum / Avoid'
        ELSE 'Neutral / Transition'
    END AS production_factor_basket
FROM final;

CREATE OR REPLACE VIEW v_vti_factor_production_membership_5b AS
SELECT ticker, company_name, sector, industry, market_cap, size_bucket, liquidity_bucket,
       production_factor_score, production_factor_quintile,
       'production_factor' AS basket_type, production_factor_basket AS basket_name,
       production_factor_score AS basket_score,
       ret_1w_pct, ret_1m_pct, ret_3m_pct, ret_6m_pct, ret_1y_pct, ret_ytd_pct,
       rsi_14, volatility_annual_polygon, dollar_volume_20d_polygon,
       production_theme, dolthub_overlay_label
FROM v_vti_factor_production_scores_5b
UNION ALL
SELECT ticker, company_name, sector, industry, market_cap, size_bucket, liquidity_bucket,
       production_factor_score, production_factor_quintile,
       'theme' AS basket_type, COALESCE(production_theme, 'No Clean Theme') AS basket_name,
       clean_theme_score AS basket_score,
       ret_1w_pct, ret_1m_pct, ret_3m_pct, ret_6m_pct, ret_1y_pct, ret_ytd_pct,
       rsi_14, volatility_annual_polygon, dollar_volume_20d_polygon,
       production_theme, dolthub_overlay_label
FROM v_vti_factor_production_scores_5b
UNION ALL
SELECT ticker, company_name, sector, industry, market_cap, size_bucket, liquidity_bucket,
       production_factor_score, production_factor_quintile,
       'size' AS basket_type, size_bucket AS basket_name,
       size_liquidity_momentum_score AS basket_score,
       ret_1w_pct, ret_1m_pct, ret_3m_pct, ret_6m_pct, ret_1y_pct, ret_ytd_pct,
       rsi_14, volatility_annual_polygon, dollar_volume_20d_polygon,
       production_theme, dolthub_overlay_label
FROM v_vti_factor_production_scores_5b
UNION ALL
SELECT ticker, company_name, sector, industry, market_cap, size_bucket, liquidity_bucket,
       production_factor_score, production_factor_quintile,
       'liquidity' AS basket_type, liquidity_bucket AS basket_name,
       liquidity_score AS basket_score,
       ret_1w_pct, ret_1m_pct, ret_3m_pct, ret_6m_pct, ret_1y_pct, ret_ytd_pct,
       rsi_14, volatility_annual_polygon, dollar_volume_20d_polygon,
       production_theme, dolthub_overlay_label
FROM v_vti_factor_production_scores_5b
UNION ALL
SELECT ticker, company_name, sector, industry, market_cap, size_bucket, liquidity_bucket,
       production_factor_score, production_factor_quintile,
       'dolthub_sparse_overlay' AS basket_type, dolthub_overlay_label AS basket_name,
       COALESCE(dolthub_overlay_score, 5.0) AS basket_score,
       ret_1w_pct, ret_1m_pct, ret_3m_pct, ret_6m_pct, ret_1y_pct, ret_ytd_pct,
       rsi_14, volatility_annual_polygon, dollar_volume_20d_polygon,
       production_theme, dolthub_overlay_label
FROM v_vti_factor_production_scores_5b;

CREATE OR REPLACE VIEW v_vti_factor_production_basket_vectors_5b AS
WITH weighted AS (
    SELECT
        *,
        market_cap / NULLIF(SUM(market_cap) OVER (PARTITION BY basket_type, basket_name), 0) AS basket_cap_weight,
        dollar_volume_20d_polygon / NULLIF(SUM(dollar_volume_20d_polygon) OVER (PARTITION BY basket_type, basket_name), 0) AS basket_liquidity_weight
    FROM v_vti_factor_production_membership_5b
)
SELECT
    basket_type,
    basket_name,
    COUNT(*) AS ticker_count,
    SUM(market_cap) AS aggregate_market_cap,
    SUM(dollar_volume_20d_polygon) AS aggregate_20d_dollar_volume,
    AVG(production_factor_score) AS avg_factor_score,
    SUM(basket_cap_weight * production_factor_score) AS cap_weighted_factor_score,
    SUM(basket_cap_weight * ret_1w_pct) AS cap_weighted_ret_1w_pct,
    SUM(basket_cap_weight * ret_1m_pct) AS cap_weighted_ret_1m_pct,
    SUM(basket_cap_weight * ret_3m_pct) AS cap_weighted_ret_3m_pct,
    SUM(basket_cap_weight * ret_6m_pct) AS cap_weighted_ret_6m_pct,
    SUM(basket_cap_weight * ret_1y_pct) AS cap_weighted_ret_1y_pct,
    SUM(basket_cap_weight * ret_ytd_pct) AS cap_weighted_ret_ytd_pct,
    SUM(basket_cap_weight * rsi_14) AS cap_weighted_rsi_14,
    SUM(basket_cap_weight * volatility_annual_polygon) AS cap_weighted_volatility_annual,
    SUM(basket_liquidity_weight * ret_1m_pct) AS liquidity_weighted_ret_1m_pct,
    SUM(basket_liquidity_weight * ret_3m_pct) AS liquidity_weighted_ret_3m_pct,
    STRING_AGG(ticker, ', ' ORDER BY market_cap DESC) AS tickers_by_market_cap
FROM weighted
GROUP BY basket_type, basket_name
ORDER BY basket_type, cap_weighted_factor_score DESC;

CREATE OR REPLACE VIEW v_vti_factor_production_relative_value_5b AS
WITH vec AS (
    SELECT *
    FROM v_vti_factor_production_basket_vectors_5b
    WHERE basket_type IN ('production_factor', 'theme')
      AND ticker_count >= 10
)
SELECT
    a.basket_type,
    a.basket_name AS basket_a,
    b.basket_name AS basket_b,
    a.cap_weighted_factor_score - b.cap_weighted_factor_score AS factor_score_spread,
    a.cap_weighted_ret_1m_pct - b.cap_weighted_ret_1m_pct AS ret_1m_spread_pct,
    a.cap_weighted_ret_3m_pct - b.cap_weighted_ret_3m_pct AS ret_3m_spread_pct,
    a.cap_weighted_ret_ytd_pct - b.cap_weighted_ret_ytd_pct AS ret_ytd_spread_pct,
    a.cap_weighted_rsi_14 - b.cap_weighted_rsi_14 AS rsi_spread,
    a.cap_weighted_volatility_annual - b.cap_weighted_volatility_annual AS vol_spread,
    a.ticker_count AS basket_a_count,
    b.ticker_count AS basket_b_count
FROM vec a
JOIN vec b
  ON a.basket_type = b.basket_type
 AND a.basket_name < b.basket_name;

CREATE OR REPLACE VIEW v_vti_factor_production_leaders_5b AS
SELECT *
FROM v_vti_factor_production_scores_5b
WHERE production_factor_quintile = 1
ORDER BY production_factor_score DESC, market_cap DESC;

CREATE OR REPLACE VIEW v_vti_factor_production_breakouts_5b AS
SELECT *
FROM v_vti_factor_production_scores_5b
WHERE production_factor_basket IN (
    'Mega-Cap Liquid Momentum Leadership',
    'Institutional Large-Cap Accumulation',
    'Smaller-Cap High-Beta Breakout',
    'Thematic Momentum Leadership'
)
ORDER BY production_factor_score DESC, market_cap DESC;

CREATE OR REPLACE VIEW v_vti_factor_production_dolthub_overlay_5b AS
SELECT *
FROM v_vti_factor_production_scores_5b
WHERE has_dolthub_vgm
ORDER BY production_factor_score DESC, market_cap DESC;
