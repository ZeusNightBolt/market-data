-- Relative value laggard analysis for production factor baskets.
-- Identifies stocks underperforming their basket using Z-score, percentile deviation, and momentum divergence.
-- Generates long ideas based on statistical mean-reversion and relative value opportunities.

CREATE OR REPLACE VIEW v_vti_basket_relative_laggards_5b AS
WITH basket_averages AS (
    SELECT
        basket_type,
        basket_name,
        AVG(production_factor_score) AS basket_avg_factor_score,
        AVG(ret_1m_pct) AS basket_avg_ret_1m_pct,
        AVG(ret_3m_pct) AS basket_avg_ret_3m_pct,
        AVG(ret_6m_pct) AS basket_avg_ret_6m_pct,
        AVG(ret_ytd_pct) AS basket_avg_ret_ytd_pct,
        AVG(rsi_14) AS basket_avg_rsi_14,
        AVG(price_vs_sma50_pct) AS basket_avg_price_vs_sma50_pct,
        AVG(price_vs_sma200_pct) AS basket_avg_price_vs_sma200_pct,
        AVG(volatility_annual_polygon) AS basket_avg_volatility,
        AVG(dollar_volume_20d_polygon) AS basket_avg_dollar_volume,
        STDDEV(ret_1m_pct) AS basket_std_ret_1m_pct,
        STDDEV(ret_3m_pct) AS basket_std_ret_3m_pct,
        STDDEV(rsi_14) AS basket_std_rsi_14,
        COUNT(*) AS basket_count
    FROM v_vti_factor_production_membership_5b
    WHERE basket_type = 'production_factor'
    GROUP BY basket_type, basket_name
),
deviations AS (
    SELECT
        m.*,
        b.basket_avg_factor_score,
        b.basket_avg_ret_1m_pct,
        b.basket_avg_ret_3m_pct,
        b.basket_avg_ret_6m_pct,
        b.basket_avg_ret_ytd_pct,
        b.basket_avg_rsi_14,
        b.basket_avg_price_vs_sma50_pct,
        b.basket_avg_price_vs_sma200_pct,
        b.basket_avg_volatility,
        b.basket_avg_dollar_volume,
        b.basket_std_ret_1m_pct,
        b.basket_std_ret_3m_pct,
        b.basket_std_rsi_14,
        b.basket_count,
        m.ret_1m_pct - b.basket_avg_ret_1m_pct AS ret_1m_deviation_pct,
        m.ret_3m_pct - b.basket_avg_ret_3m_pct AS ret_3m_deviation_pct,
        m.ret_6m_pct - b.basket_avg_ret_6m_pct AS ret_6m_deviation_pct,
        m.ret_ytd_pct - b.basket_avg_ret_ytd_pct AS ret_ytd_deviation_pct,
        m.rsi_14 - b.basket_avg_rsi_14 AS rsi_deviation,
        m.price_vs_sma50_pct - b.basket_avg_price_vs_sma50_pct AS price_vs_sma50_deviation_pct,
        m.price_vs_sma200_pct - b.basket_avg_price_vs_sma200_pct AS price_vs_sma200_deviation_pct,
        CASE
            WHEN b.basket_std_ret_1m_pct > 0 THEN (m.ret_1m_pct - b.basket_avg_ret_1m_pct) / b.basket_std_ret_1m_pct
            ELSE 0
        END AS ret_1m_z_score,
        CASE
            WHEN b.basket_std_ret_3m_pct > 0 THEN (m.ret_3m_pct - b.basket_avg_ret_3m_pct) / b.basket_std_ret_3m_pct
            ELSE 0
        END AS ret_3m_z_score,
        CASE
            WHEN b.basket_std_rsi_14 > 0 THEN (m.rsi_14 - b.basket_avg_rsi_14) / b.basket_std_rsi_14
            ELSE 0
        END AS rsi_z_score,
        m.production_factor_score - b.basket_avg_factor_score AS factor_score_deviation
    FROM v_vti_factor_production_scores_5b m
    JOIN basket_averages b ON m.production_factor_basket = b.basket_name
    WHERE m.production_factor_basket IS NOT NULL
),
scored AS (
    SELECT
        *,
        -- Combined laggard score: lower is more underperforming
        (-1.0 * 
            (0.30 * COALESCE(ret_1m_z_score, 0.0) +
             0.25 * COALESCE(ret_3m_z_score, 0.0) +
             0.20 * COALESCE(factor_score_deviation, 0.0) +
             0.15 * COALESCE(rsi_z_score, 0.0) +
             0.10 * COALESCE(ret_ytd_deviation_pct / NULLIF(ABS(basket_avg_ret_ytd_pct), 0), 0.0))
        ) AS combined_laggard_score,
        -- Mean reversion potential: RSI + price position + recent momentum weakness
        CASE
            WHEN rsi_14 < 50 AND ret_1m_deviation_pct < -20 AND ret_3m_deviation_pct < -15 THEN 'High Mean Reversion Potential'
            WHEN rsi_14 < 60 AND ret_1m_deviation_pct < -15 AND ret_3m_deviation_pct < -10 THEN 'Moderate Mean Reversion Potential'
            WHEN rsi_14 > 70 AND ret_1m_deviation_pct > 20 AND ret_3m_deviation_pct > 15 THEN 'Overextended - Short Risk'
            ELSE 'Neutral'
        END AS mean_reversion_regime,
        -- Relative value assessment
        CASE
            WHEN production_factor_score > b.basket_avg_factor_score * 0.9 AND ret_1m_deviation_pct < -10 THEN 'Strong Fundamentals, Weak Price'
            WHEN production_factor_score < b.basket_avg_factor_score * 0.8 AND ret_1m_deviation_pct < -15 THEN 'Weak Fundamentals & Price - Avoid'
            WHEN production_factor_score > b.basket_avg_factor_score * 1.1 AND ret_1m_deviation_pct < -5 THEN 'High Quality Relative Laggard'
            ELSE 'Mixed Fundamentals/Price'
        END AS relative_value_assessment,
        -- Basket momentum divergence
        CASE
            WHEN ret_1m_deviation_pct < -15 AND ret_3m_deviation_pct < -10 AND basket_avg_ret_1m_pct > 10 THEN 'Severe Underperformance vs Strong Basket'
            WHEN ret_1m_deviation_pct < -10 AND ret_3m_deviation_pct < -5 AND basket_avg_ret_3m_pct > 15 THEN 'Moderate Underperformance vs Strong Basket'
            WHEN ret_1m_deviation_pct > 15 AND ret_3m_deviation_pct > 10 THEN 'Outperforming Basket - Not Laggard'
            ELSE 'Normal Variation'
        END AS momentum_divergence,
        -- Recent acceleration/deceleration
        CASE
            WHEN ret_1m_deviation_pct < -10 AND ret_3m_deviation_pct < -5 THEN 'Decelerating Underperformance'
            WHEN ret_1m_deviation_pct < -20 AND ret_3m_deviation_pct > -5 THEN 'Recent Momentum Collapse'
            WHEN ret_1m_deviation_pct > -5 AND ret_3m_deviation_pct < -15 THEN 'Recovery Attempt'
            ELSE 'Stable Underperformance'
        END AS momentum_trend,
        -- Valuation vs basket
        CASE
            WHEN price_vs_sma200_pct < basket_avg_price_vs_sma200_pct - 10 AND rsi_14 < basket_avg_rsi_14 THEN 'Deep Value vs Basket'
            WHEN price_vs_sma200_pct > basket_avg_price_vs_sma200_pct + 10 AND rsi_14 > basket_avg_rsi_14 THEN 'Overvalued vs Basket'
            ELSE 'Fair Value vs Basket'
        END as valuation_vs_basket
    FROM deviations
),
ranked AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY basket_type, basket_name
            ORDER BY combined_laggard_score ASC, ret_1m_deviation_pct ASC, ret_3m_deviation_pct ASC
        ) AS laggard_rank_in_basket,
        ROW_NUMBER() OVER (
            PARTITION BY basket_type, basket_name
            ORDER BY ret_1m_deviation_pct ASC, ret_3m_deviation_pct ASC
        ) AS worst_return_rank_in_basket,
        CUME_DIST() OVER (PARTITION BY basket_type, basket_name ORDER BY combined_laggard_score ASC) AS laggard_percentile_in_basket
    FROM scored
)
SELECT
    ticker,
    company_name,
    sector,
    industry,
    market_cap,
    size_bucket,
    liquidity_bucket,
    production_theme,
    production_factor_basket,
    basket_type,
    basket_name,
    -- Deviations from basket
    ret_1m_deviation_pct,
    ret_3m_deviation_pct,
    ret_6m_deviation_pct,
    ret_ytd_deviation_pct,
    rsi_deviation,
    price_vs_sma50_deviation_pct,
    price_vs_sma200_deviation_pct,
    factor_score_deviation,
    -- Statistical scores
    ret_1m_z_score,
    ret_3m_z_score,
    rsi_z_score,
    combined_laggard_score,
    -- Analysis flags
    mean_reversion_regime,
    relative_value_assessment,
    momentum_divergence,
    momentum_trend,
    valuation_vs_basket,
    -- Rankings
    laggard_rank_in_basket,
    worst_return_rank_in_basket,
    laggard_percentile_in_basket,
    -- Basket context
    basket_avg_ret_1m_pct,
    basket_avg_ret_3m_pct,
    basket_avg_rsi_14,
    basket_count,
    -- Recent performance for comparison
    ret_1m_pct,
    ret_3m_pct,
    rsi_14,
    production_factor_score,
    production_factor_quintile,
    dolthub_overlay_label
FROM ranked
ORDER BY basket_type, basket_name, laggard_rank_in_basket;

CREATE OR REPLACE VIEW v_vti_basket_relative_long_ideas_5b AS
WITH laggards AS (
    SELECT *
    FROM v_vti_basket_relative_laggards_5b
    WHERE laggard_rank_in_basket <= 10
      AND momentum_divergence IN ('Severe Underperformance vs Strong Basket', 'Moderate Underperformance vs Strong Basket')
      AND mean_reversion_regime IN ('High Mean Reversion Potential', 'Moderate Mean Reversion Potential')
      AND relative_value_assessment IN ('Strong Fundamentals, Weak Price', 'High Quality Relative Laggard')
    ORDER BY basket_type, basket_name, combined_laggard_score ASC
),
ideas AS (
    SELECT
        *,
        -- Long idea rationale
        CASE
            WHEN mean_reversion_regime = 'High Mean Reversion Potential' AND ret_1m_deviation_pct < -20 THEN 'Mean Reversion: Severe Oversold with Positive Divergence'
            WHEN mean_reversion_regime = 'Moderate Mean Reversion Potential' AND ret_1m_deviation_pct < -15 THEN 'Mean Reversion: Technical Oversold vs Basket'
            WHEN relative_value_assessment = 'High Quality Relative Laggard' AND production_factor_score > 6.0 THEN 'Quality Value: Strong Fundamentals Lagging Basket Momentum'
            WHEN relative_value_assessment = 'Strong Fundamentals, Weak Price' AND basket_avg_ret_1m_pct > 10 THEN 'Relative Momentum: Basket Strength with Individual Weakness'
            WHEN momentum_trend = 'Recovery Attempt' AND ret_1m_deviation_pct > -5 AND ret_3m_deviation_pct < -10 THEN 'Momentum Inflection: Recent Strength in Persistent Weakness'
            ELSE 'Relative Value Basket Diversification'
        END AS long_idea_rationale,
        -- Expected catalysts
        CASE
            WHEN rsi_14 < 35 AND ret_1m_deviation_pct < -20 THEN 'Catalyst: RSI Oversold Mean Reversion'
            WHEN price_vs_sma200_pct < -20 AND ret_3m_deviation_pct < -15 THEN 'Catalyst: Major Support Level Test'
            WHEN ret_1m_deviation_pct < -25 AND basket_avg_ret_1m_pct > 15 THEN 'Catalyst: Relative Strength Reversal'
            WHEN production_factor_score > 7.0 AND ret_1m_deviation_pct < -10 THEN 'Catalyst: Fundamental Divergence Correction'
            ELSE 'Catalyst: Basket Momentum Normalization'
        END AS catalyst,
        -- Risk assessment
        CASE
            WHEN volatility_annual_polygon > 0.50 THEN 'High Volatility Risk'
            WHEN market_cap < 10000000000 THEN 'Market Cap Risk'
            WHEN rsi_14 > 70 THEN 'Overbought Risk'
            WHEN ret_3m_deviation_pct < -25 THEN 'Deep Underperformance Risk'
            ELSE 'Moderate Risk'
        END AS risk_assessment,
        -- Time horizon
        CASE
            WHEN rsi_14 < 35 THEN 'Short-Term (1-4 weeks)'
            WHEN ret_1m_deviation_pct < -20 THEN 'Medium-Term (1-2 months)'
            WHEN ret_3m_deviation_pct < -15 THEN 'Long-Term (3-6 months)'
            ELSE 'Medium-Term (1-3 months)'
        END as expected_time_horizon
    FROM laggards
)
SELECT
    ticker,
    company_name,
    sector,
    industry,
    market_cap,
    size_bucket,
    liquidity_bucket,
    production_theme,
    production_factor_basket,
    basket_name,
    -- Core metrics
    combined_laggard_score,
    ret_1m_deviation_pct,
    ret_3m_deviation_pct,
    ret_1m_z_score,
    ret_3m_z_score,
    rsi_14,
    production_factor_score,
    -- Analysis
    mean_reversion_regime,
    relative_value_assessment,
    momentum_divergence,
    momentum_trend,
    valuation_vs_basket,
    -- Long idea
    long_idea_rationale,
    catalyst,
    risk_assessment,
    expected_time_horizon,
    -- Basket context
    basket_avg_ret_1m_pct,
    basket_avg_ret_3m_pct,
    basket_avg_rsi_14,
    basket_count,
    -- Additional context
    ret_1m_pct,
    ret_3m_pct,
    ret_ytd_pct,
    price_vs_sma50_pct,
    price_vs_sma200_pct,
    dolthub_overlay_label
FROM ideas
ORDER BY combined_laggard_score ASC, ret_1m_deviation_pct ASC, ret_3m_deviation_pct ASC;

CREATE OR REPLACE VIEW v_vti_basket_long_ideas_summary_5b AS
SELECT
    basket_name,
    COUNT(*) AS idea_count,
    SUM(market_cap) AS aggregate_market_cap,
    AVG(production_factor_score) AS avg_factor_score,
    AVG(ret_1m_deviation_pct) AS avg_ret_1m_deviation,
    AVG(ret_3m_deviation_pct) AS avg_ret_3m_deviation,
    AVG(rsi_14) AS avg_rsi_14,
    STRING_AGG(ticker, ', ' ORDER BY combined_laggard_score ASC) AS tickers,
    STRING_AGG(production_theme, ', ' ORDER BY combined_laggard_score ASC) AS themes,
    STRING_AGG(long_idea_rationale, '. ' ORDER BY combined_laggard_score ASC) AS idea_rationales
FROM v_vti_basket_relative_long_ideas_5b
GROUP BY basket_name
ORDER BY idea_count DESC, avg_ret_1m_deviation ASC;
