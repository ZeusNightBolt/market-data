-- Quant factor basket views for classified VTI tickers > $5B market cap.
-- Builds orthogonal baskets from actual price movement, SMA/Keltner/RSI state,
-- sparse DoltHub V/G/M grades, and filtered keyword-factor themes.

CREATE OR REPLACE VIEW v_vti_factor_features_5b AS
WITH universe AS (
    SELECT *
    FROM v_vti_sector_universe_5b
), bars_ranked AS (
    SELECT
        d.ticker,
        d.timestamp,
        epoch_ms(d.timestamp)::DATE AS price_day,
        d.close,
        ROW_NUMBER() OVER (PARTITION BY d.ticker ORDER BY d.timestamp DESC) AS rn,
        MAX(d.timestamp) OVER (PARTITION BY d.ticker) AS latest_ts
    FROM daily_bars d
    JOIN universe u USING (ticker)
    WHERE d.close IS NOT NULL AND d.close > 0
), latest AS (
    SELECT ticker, timestamp AS latest_timestamp, price_day AS latest_price_day, close AS latest_close
    FROM bars_ranked
    WHERE rn = 1
), returns AS (
    SELECT
        l.ticker,
        l.latest_price_day,
        l.latest_close,
        MAX(CASE WHEN b.rn = 6 THEN b.close END) AS close_1w_ago,
        MAX(CASE WHEN b.rn = 22 THEN b.close END) AS close_1m_ago,
        MAX(CASE WHEN b.rn = 64 THEN b.close END) AS close_3m_ago,
        MAX(CASE WHEN b.rn = 127 THEN b.close END) AS close_6m_ago,
        MAX(CASE WHEN b.rn = 253 THEN b.close END) AS close_1y_ago,
        arg_min(b.close, b.timestamp) FILTER (WHERE date_part('year', b.price_day) = date_part('year', l.latest_price_day)) AS close_ytd_start
    FROM latest l
    JOIN bars_ranked b USING (ticker)
    GROUP BY l.ticker, l.latest_price_day, l.latest_close
), raw_features AS (
    SELECT
        u.*,
        r.latest_price_day,
        r.latest_close,
        100.0 * (r.latest_close / NULLIF(r.close_1w_ago, 0) - 1.0) AS ret_1w_pct,
        100.0 * (r.latest_close / NULLIF(r.close_1m_ago, 0) - 1.0) AS ret_1m_pct,
        100.0 * (r.latest_close / NULLIF(r.close_3m_ago, 0) - 1.0) AS ret_3m_pct,
        100.0 * (r.latest_close / NULLIF(r.close_6m_ago, 0) - 1.0) AS ret_6m_pct,
        100.0 * (r.latest_close / NULLIF(r.close_1y_ago, 0) - 1.0) AS ret_1y_pct,
        100.0 * (r.latest_close / NULLIF(r.close_ytd_start, 0) - 1.0) AS ret_ytd_pct,
        rich.rank,
        rich.value_grade,
        rich.growth_grade,
        rich.momentum_grade,
        rich.vgm_grade,
        rich.polygon_keywords_json,
        rich.keltner_upper,
        rich.keltner_lower,
        CASE upper(nullif(rich.value_grade, ''))
            WHEN 'A' THEN 10.0 WHEN 'B' THEN 7.5 WHEN 'C' THEN 5.0 WHEN 'D' THEN 2.5 WHEN 'F' THEN 0.0 ELSE NULL END AS dolt_value_score,
        CASE upper(nullif(rich.growth_grade, ''))
            WHEN 'A' THEN 10.0 WHEN 'B' THEN 7.5 WHEN 'C' THEN 5.0 WHEN 'D' THEN 2.5 WHEN 'F' THEN 0.0 ELSE NULL END AS dolt_growth_score,
        CASE upper(nullif(rich.momentum_grade, ''))
            WHEN 'A' THEN 10.0 WHEN 'B' THEN 7.5 WHEN 'C' THEN 5.0 WHEN 'D' THEN 2.5 WHEN 'F' THEN 0.0 ELSE NULL END AS dolt_momentum_score,
        CASE
            WHEN u.keyword_factor_baskets LIKE '%Semiconductors%' THEN 'Semiconductors'
            WHEN u.keyword_factor_baskets LIKE '%AI Infrastructure%' THEN 'AI Infrastructure'
            WHEN u.keyword_factor_baskets LIKE '%Cybersecurity%' THEN 'Cybersecurity'
            WHEN u.keyword_factor_baskets LIKE '%Cloud Software%' THEN 'Cloud Software'
            WHEN u.keyword_factor_baskets LIKE '%Crypto / Digital Assets%' THEN 'Crypto / Digital Assets'
            WHEN u.keyword_factor_baskets LIKE '%Healthcare / Biotech%' THEN 'Healthcare / Biotech'
            WHEN u.keyword_factor_baskets LIKE '%Defense / Aerospace%' THEN 'Defense / Aerospace'
            WHEN u.keyword_factor_baskets LIKE '%Industrial Automation%' THEN 'Industrial Automation'
            WHEN u.keyword_factor_baskets LIKE '%Energy Transition%' THEN 'Energy Transition'
            WHEN u.keyword_factor_baskets LIKE '%Oil & Gas%' THEN 'Oil & Gas'
            WHEN u.keyword_factor_baskets LIKE '%Financials / Credit%' THEN 'Financials / Credit'
            WHEN u.keyword_factor_baskets LIKE '%Consumer / Retail%' THEN 'Consumer / Retail'
            WHEN u.keyword_factor_baskets LIKE '%Housing / Real Estate%' THEN 'Housing / Real Estate'
            WHEN u.keyword_factor_baskets LIKE '%Commodities / Materials%' THEN 'Commodities / Materials'
            ELSE NULL
        END AS clean_keyword_theme
    FROM universe u
    JOIN returns r USING (ticker)
    LEFT JOIN v_vti_ticker_rich rich USING (ticker)
), ranked AS (
    SELECT
        *,
        CUME_DIST() OVER (ORDER BY ret_1m_pct NULLS FIRST) AS ret_1m_pctile,
        CUME_DIST() OVER (ORDER BY ret_3m_pct NULLS FIRST) AS ret_3m_pctile,
        CUME_DIST() OVER (ORDER BY ret_6m_pct NULLS FIRST) AS ret_6m_pctile,
        CUME_DIST() OVER (ORDER BY ret_ytd_pct NULLS FIRST) AS ret_ytd_pctile,
        CUME_DIST() OVER (ORDER BY volatility_annual_polygon DESC NULLS LAST) AS low_vol_pctile,
        CUME_DIST() OVER (ORDER BY dollar_volume_20d_polygon NULLS FIRST) AS liquidity_pctile
    FROM raw_features
), scored AS (
    SELECT
        *,
        10.0 * (
            0.35 * COALESCE(ret_1m_pctile, 0.5) +
            0.30 * COALESCE(ret_3m_pctile, 0.5) +
            0.20 * COALESCE(ret_6m_pctile, 0.5) +
            0.15 * COALESCE(ret_ytd_pctile, 0.5)
        ) AS price_momentum_score,
        LEAST(10.0, GREATEST(0.0,
            (CASE WHEN close > sma_20 THEN 2.0 ELSE 0.0 END) +
            (CASE WHEN close > sma_50 THEN 1.5 ELSE 0.0 END) +
            (CASE WHEN close > sma_200 THEN 1.5 ELSE 0.0 END) +
            (CASE WHEN sma_20 > sma_50 THEN 1.5 ELSE 0.0 END) +
            (CASE WHEN sma_50 > sma_200 THEN 1.5 ELSE 0.0 END) +
            (CASE WHEN close > keltner_upper THEN 1.0 WHEN close < keltner_lower THEN -1.5 ELSE 0.0 END) +
            (CASE WHEN price_vs_sma200_pct > 20 THEN 1.0 WHEN price_vs_sma200_pct < -10 THEN -1.0 ELSE 0.0 END)
        )) AS trend_structure_score,
        CASE
            WHEN rsi_14 BETWEEN 55 AND 72 THEN 10.0
            WHEN rsi_14 BETWEEN 50 AND 55 THEN 8.0
            WHEN rsi_14 BETWEEN 72 AND 80 THEN 7.0
            WHEN rsi_14 BETWEEN 40 AND 50 THEN 5.0
            WHEN rsi_14 BETWEEN 30 AND 40 THEN 4.0
            WHEN rsi_14 > 80 THEN 3.0
            WHEN rsi_14 < 30 THEN 3.0
            ELSE 5.0
        END AS rsi_regime_score,
        10.0 * COALESCE(low_vol_pctile, 0.5) AS vol_risk_score,
        10.0 * COALESCE(liquidity_pctile, 0.5) AS liquidity_score
    FROM ranked
), final AS (
    SELECT
        *,
        (
            0.28 * price_momentum_score +
            0.22 * trend_structure_score +
            0.12 * rsi_regime_score +
            0.10 * vol_risk_score +
            0.08 * liquidity_score +
            0.07 * COALESCE(dolt_momentum_score, 5.0) +
            0.07 * COALESCE(dolt_growth_score, 5.0) +
            0.06 * COALESCE(dolt_value_score, 5.0)
        ) AS quant_factor_score,
        CASE WHEN dolt_value_score IS NULL AND dolt_growth_score IS NULL AND dolt_momentum_score IS NULL THEN FALSE ELSE TRUE END AS has_dolthub_vgm
    FROM scored
)
SELECT
    *,
    NTILE(5) OVER (ORDER BY quant_factor_score DESC NULLS LAST) AS quant_factor_quintile,
    CASE
        WHEN ret_1m_pctile >= 0.80 AND ret_3m_pctile >= 0.70 AND close > sma_20 AND close > sma_50 AND close > sma_200 AND rsi_14 BETWEEN 55 AND 80 THEN 'Momentum Breakout / Leadership'
        WHEN close > sma_20 AND close > sma_50 AND close > sma_200 AND sma_20 > sma_50 AND sma_50 > sma_200 AND rsi_14 BETWEEN 50 AND 75 THEN 'Persistent Uptrend / Compounder'
        WHEN rsi_14 > 80 OR (close > keltner_upper AND ret_1m_pctile >= 0.90) THEN 'Extended Overbought Momentum'
        WHEN rsi_14 < 35 OR close < keltner_lower THEN 'Oversold / Mean-Reversion Watch'
        WHEN close < sma_50 AND close < sma_200 AND ret_3m_pctile <= 0.30 THEN 'Weak Downtrend / Avoid Momentum'
        ELSE 'Neutral / Transition'
    END AS price_pattern_basket,
    CASE
        WHEN COALESCE(dolt_growth_score, -1) >= 7.5 AND COALESCE(dolt_momentum_score, -1) >= 7.5 THEN 'DoltHub Growth + Momentum'
        WHEN COALESCE(dolt_value_score, -1) >= 7.5 AND ret_1m_pctile >= 0.60 THEN 'DoltHub Value Catch-Up'
        WHEN COALESCE(dolt_value_score, -1) >= 7.5 AND COALESCE(dolt_growth_score, -1) >= 7.5 THEN 'DoltHub Balanced V/G'
        WHEN has_dolthub_vgm THEN 'DoltHub Mixed/Weak VGM'
        ELSE 'No DoltHub VGM Coverage'
    END AS dolthub_style_basket,
    CASE
        WHEN close > sma_50 AND close > sma_200 AND low_vol_pctile >= 0.70 THEN 'Low-Vol Uptrend'
        WHEN price_momentum_score >= 8.0 AND volatility_annual_polygon >= 0.45 THEN 'High-Vol Momentum'
        WHEN price_momentum_score <= 3.0 AND volatility_annual_polygon >= 0.45 THEN 'High-Vol Breakdown'
        WHEN ABS(COALESCE(price_vs_sma50_pct, 0)) <= 5 AND rsi_14 BETWEEN 40 AND 60 THEN 'Base Building / Coiled'
        ELSE 'Normal Vol/Trend'
    END AS trend_vol_basket,
    COALESCE(clean_keyword_theme, 'No Clean Keyword Theme') AS thematic_basket
FROM final;

CREATE OR REPLACE VIEW v_vti_factor_basket_membership_5b AS
SELECT ticker, company_name, sector, industry, market_cap, quant_factor_score, quant_factor_quintile,
       'price_pattern' AS basket_type, price_pattern_basket AS basket_name,
       price_momentum_score AS basket_score, ret_1m_pct, ret_3m_pct, ret_6m_pct, ret_ytd_pct,
       rsi_14, price_vs_sma20_pct, price_vs_sma50_pct, price_vs_sma200_pct,
       thematic_basket, keyword_factor_baskets
FROM v_vti_factor_features_5b
UNION ALL
SELECT ticker, company_name, sector, industry, market_cap, quant_factor_score, quant_factor_quintile,
       'dolthub_style' AS basket_type, dolthub_style_basket AS basket_name,
       COALESCE((COALESCE(dolt_value_score,5)+COALESCE(dolt_growth_score,5)+COALESCE(dolt_momentum_score,5))/3,5) AS basket_score,
       ret_1m_pct, ret_3m_pct, ret_6m_pct, ret_ytd_pct,
       rsi_14, price_vs_sma20_pct, price_vs_sma50_pct, price_vs_sma200_pct,
       thematic_basket, keyword_factor_baskets
FROM v_vti_factor_features_5b
UNION ALL
SELECT ticker, company_name, sector, industry, market_cap, quant_factor_score, quant_factor_quintile,
       'trend_vol' AS basket_type, trend_vol_basket AS basket_name,
       0.5 * trend_structure_score + 0.5 * vol_risk_score AS basket_score,
       ret_1m_pct, ret_3m_pct, ret_6m_pct, ret_ytd_pct,
       rsi_14, price_vs_sma20_pct, price_vs_sma50_pct, price_vs_sma200_pct,
       thematic_basket, keyword_factor_baskets
FROM v_vti_factor_features_5b
UNION ALL
SELECT ticker, company_name, sector, industry, market_cap, quant_factor_score, quant_factor_quintile,
       'theme' AS basket_type, thematic_basket AS basket_name,
       COALESCE(primary_keyword_factor_score, 0.0) AS basket_score,
       ret_1m_pct, ret_3m_pct, ret_6m_pct, ret_ytd_pct,
       rsi_14, price_vs_sma20_pct, price_vs_sma50_pct, price_vs_sma200_pct,
       thematic_basket, keyword_factor_baskets
FROM v_vti_factor_features_5b;

CREATE OR REPLACE VIEW v_vti_factor_basket_summary_5b AS
SELECT
    basket_type,
    basket_name,
    COUNT(*) AS ticker_count,
    SUM(market_cap) AS aggregate_market_cap,
    AVG(quant_factor_score) AS avg_quant_factor_score,
    AVG(basket_score) AS avg_basket_score,
    AVG(ret_1m_pct) AS avg_ret_1m_pct,
    AVG(ret_3m_pct) AS avg_ret_3m_pct,
    AVG(ret_6m_pct) AS avg_ret_6m_pct,
    AVG(ret_ytd_pct) AS avg_ret_ytd_pct,
    AVG(rsi_14) AS avg_rsi_14,
    AVG(price_vs_sma50_pct) AS avg_price_vs_sma50_pct,
    AVG(price_vs_sma200_pct) AS avg_price_vs_sma200_pct,
    STRING_AGG(ticker, ', ' ORDER BY market_cap DESC) AS tickers_by_market_cap
FROM v_vti_factor_basket_membership_5b
GROUP BY basket_type, basket_name
ORDER BY basket_type, avg_quant_factor_score DESC;

CREATE OR REPLACE VIEW v_vti_factor_leaders_5b AS
SELECT *
FROM v_vti_factor_features_5b
WHERE quant_factor_quintile = 1
ORDER BY quant_factor_score DESC, market_cap DESC;

CREATE OR REPLACE VIEW v_vti_momentum_breakouts_5b AS
SELECT *
FROM v_vti_factor_features_5b
WHERE price_pattern_basket = 'Momentum Breakout / Leadership'
ORDER BY price_momentum_score DESC, quant_factor_score DESC;

CREATE OR REPLACE VIEW v_vti_oversold_mean_reversion_5b AS
SELECT *
FROM v_vti_factor_features_5b
WHERE price_pattern_basket = 'Oversold / Mean-Reversion Watch'
ORDER BY rsi_14 ASC NULLS LAST, ret_1m_pct ASC NULLS LAST;
