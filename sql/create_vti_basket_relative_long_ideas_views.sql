-- Production basket relative-value laggard views.
-- Methodology: within each production factor basket, estimate expected 1M return from
-- basket-relative 3M return, production factor score, size, liquidity, and RSI using
-- additive single-factor DuckDB REGR_SLOPE terms. Negative residuals identify tickers
-- lagging what the basket/feature mix implies. Top 10 per basket are exposed as long ideas.

CREATE OR REPLACE VIEW v_vti_basket_relative_laggards_5b AS
WITH base AS (
    SELECT
        as_of_date,
        ticker,
        company_name,
        holding_name,
        sector,
        industry,
        sub_industry,
        market_cap,
        close,
        volume,
        avg_vol_20,
        dollar_volume,
        dollar_volume_20d_polygon,
        COALESCE(dollar_volume_20d_polygon, dollar_volume, close * volume) AS effective_dollar_volume,
        size_bucket,
        liquidity_bucket,
        production_theme,
        dolthub_overlay_label,
        dolthub_overlay_score,
        clean_keyword_theme,
        primary_keyword_factor,
        primary_keyword_factor_score,
        keyword_factor_baskets,
        production_factor_basket,
        production_factor_score,
        production_factor_quintile,
        ret_1w_pct,
        ret_1m_pct,
        ret_3m_pct,
        ret_6m_pct,
        ret_1y_pct,
        ret_ytd_pct,
        rsi_14,
        price_vs_sma20_pct,
        price_vs_sma50_pct,
        price_vs_sma200_pct,
        from_52w_high_pct,
        from_52w_low_pct,
        volume_vs_20d,
        volatility_annual_polygon,
        short_interest,
        days_to_cover,
        short_pct_float,
        sentiment_score,
        sentiment_articles,
        polygon_keyword_count,
        LN(GREATEST(market_cap, 1)) AS log_market_cap,
        LN(GREATEST(COALESCE(dollar_volume_20d_polygon, dollar_volume, close * volume, 1), 1)) AS log_dollar_volume
    FROM v_vti_factor_production_scores_5b
    WHERE market_cap >= 5000000000
      AND production_factor_basket IS NOT NULL
      AND production_factor_basket <> ''
      AND ret_1m_pct IS NOT NULL
      AND ret_3m_pct IS NOT NULL
      AND production_factor_score IS NOT NULL
),
basket_stats AS (
    SELECT
        production_factor_basket,
        COUNT(*) AS basket_count,
        AVG(ret_1m_pct) AS basket_avg_ret_1m_pct,
        STDDEV_SAMP(ret_1m_pct) AS basket_std_ret_1m_pct,
        AVG(ret_3m_pct) AS basket_avg_ret_3m_pct,
        STDDEV_SAMP(ret_3m_pct) AS basket_std_ret_3m_pct,
        AVG(ret_6m_pct) AS basket_avg_ret_6m_pct,
        AVG(ret_ytd_pct) AS basket_avg_ret_ytd_pct,
        AVG(rsi_14) AS basket_avg_rsi_14,
        STDDEV_SAMP(rsi_14) AS basket_std_rsi_14,
        AVG(production_factor_score) AS basket_avg_factor_score,
        STDDEV_SAMP(production_factor_score) AS basket_std_factor_score,
        AVG(log_market_cap) AS basket_avg_log_market_cap,
        STDDEV_SAMP(log_market_cap) AS basket_std_log_market_cap,
        AVG(log_dollar_volume) AS basket_avg_log_dollar_volume,
        STDDEV_SAMP(log_dollar_volume) AS basket_std_log_dollar_volume,
        AVG(price_vs_sma50_pct) AS basket_avg_price_vs_sma50_pct,
        AVG(price_vs_sma200_pct) AS basket_avg_price_vs_sma200_pct
    FROM base
    GROUP BY production_factor_basket
),
basket_regression AS (
    SELECT
        production_factor_basket,
        COALESCE(REGR_SLOPE(ret_1m_pct, ret_3m_pct), 0) AS beta_1m_vs_3m,
        COALESCE(REGR_SLOPE(ret_1m_pct, production_factor_score), 0) AS beta_1m_vs_factor_score,
        COALESCE(REGR_SLOPE(ret_1m_pct, log_market_cap), 0) AS beta_1m_vs_log_market_cap,
        COALESCE(REGR_SLOPE(ret_1m_pct, log_dollar_volume), 0) AS beta_1m_vs_log_dollar_volume,
        COALESCE(REGR_SLOPE(ret_1m_pct, rsi_14), 0) AS beta_1m_vs_rsi
    FROM base
    GROUP BY production_factor_basket
),
scored AS (
    SELECT
        b.*,
        s.basket_count,
        s.basket_avg_ret_1m_pct,
        s.basket_std_ret_1m_pct,
        s.basket_avg_ret_3m_pct,
        s.basket_std_ret_3m_pct,
        s.basket_avg_ret_6m_pct,
        s.basket_avg_ret_ytd_pct,
        s.basket_avg_rsi_14,
        s.basket_std_rsi_14,
        s.basket_avg_factor_score,
        s.basket_std_factor_score,
        s.basket_avg_log_market_cap,
        s.basket_std_log_market_cap,
        s.basket_avg_log_dollar_volume,
        s.basket_std_log_dollar_volume,
        s.basket_avg_price_vs_sma50_pct,
        s.basket_avg_price_vs_sma200_pct,
        r.beta_1m_vs_3m,
        r.beta_1m_vs_factor_score,
        r.beta_1m_vs_log_market_cap,
        r.beta_1m_vs_log_dollar_volume,
        r.beta_1m_vs_rsi,
        (b.ret_1m_pct - s.basket_avg_ret_1m_pct) AS ret_1m_deviation_pct,
        (b.ret_3m_pct - s.basket_avg_ret_3m_pct) AS ret_3m_deviation_pct,
        (b.ret_6m_pct - s.basket_avg_ret_6m_pct) AS ret_6m_deviation_pct,
        (b.ret_ytd_pct - s.basket_avg_ret_ytd_pct) AS ret_ytd_deviation_pct,
        (b.rsi_14 - s.basket_avg_rsi_14) AS rsi_deviation,
        (b.production_factor_score - s.basket_avg_factor_score) AS factor_score_deviation,
        (b.log_market_cap - s.basket_avg_log_market_cap) AS log_market_cap_deviation,
        (b.log_dollar_volume - s.basket_avg_log_dollar_volume) AS log_dollar_volume_deviation,
        CASE WHEN s.basket_std_ret_1m_pct > 0 THEN (b.ret_1m_pct - s.basket_avg_ret_1m_pct) / s.basket_std_ret_1m_pct ELSE 0 END AS ret_1m_z_score,
        CASE WHEN s.basket_std_ret_3m_pct > 0 THEN (b.ret_3m_pct - s.basket_avg_ret_3m_pct) / s.basket_std_ret_3m_pct ELSE 0 END AS ret_3m_z_score,
        CASE WHEN s.basket_std_rsi_14 > 0 THEN (b.rsi_14 - s.basket_avg_rsi_14) / s.basket_std_rsi_14 ELSE 0 END AS rsi_z_score,
        CASE WHEN s.basket_std_factor_score > 0 THEN (b.production_factor_score - s.basket_avg_factor_score) / s.basket_std_factor_score ELSE 0 END AS factor_score_z_score,
        CASE WHEN s.basket_std_log_market_cap > 0 THEN (b.log_market_cap - s.basket_avg_log_market_cap) / s.basket_std_log_market_cap ELSE 0 END AS size_z_score,
        CASE WHEN s.basket_std_log_dollar_volume > 0 THEN (b.log_dollar_volume - s.basket_avg_log_dollar_volume) / s.basket_std_log_dollar_volume ELSE 0 END AS liquidity_z_score,
        (
            s.basket_avg_ret_1m_pct
            + r.beta_1m_vs_3m * (b.ret_3m_pct - s.basket_avg_ret_3m_pct)
            + r.beta_1m_vs_factor_score * (b.production_factor_score - s.basket_avg_factor_score)
            + r.beta_1m_vs_log_market_cap * (b.log_market_cap - s.basket_avg_log_market_cap)
            + r.beta_1m_vs_log_dollar_volume * (b.log_dollar_volume - s.basket_avg_log_dollar_volume)
            + r.beta_1m_vs_rsi * (b.rsi_14 - s.basket_avg_rsi_14)
        ) AS expected_ret_1m_pct
    FROM base b
    JOIN basket_stats s USING (production_factor_basket)
    JOIN basket_regression r USING (production_factor_basket)
),
laggard_scores AS (
    SELECT
        *,
        (ret_1m_pct - expected_ret_1m_pct) AS regression_residual_1m_pct,
        CASE WHEN basket_std_ret_1m_pct > 0 THEN (ret_1m_pct - expected_ret_1m_pct) / basket_std_ret_1m_pct ELSE 0 END AS regression_residual_z_score,
        -- Larger positive score = better relative-value laggard candidate.
        (
            0.40 * GREATEST(-(CASE WHEN basket_std_ret_1m_pct > 0 THEN (ret_1m_pct - expected_ret_1m_pct) / basket_std_ret_1m_pct ELSE 0 END), 0)
            + 0.20 * GREATEST(-ret_1m_z_score, 0)
            + 0.15 * GREATEST(-ret_3m_z_score, 0)
            + 0.10 * GREATEST(factor_score_z_score, 0)
            + 0.075 * GREATEST(size_z_score, 0)
            + 0.075 * GREATEST(liquidity_z_score, 0)
        ) AS relative_value_laggard_score,
        CASE
            WHEN (ret_1m_pct - expected_ret_1m_pct) <= -20 THEN 'Severe negative regression residual'
            WHEN (ret_1m_pct - expected_ret_1m_pct) <= -10 THEN 'Moderate negative regression residual'
            WHEN ret_1m_z_score <= -1 THEN 'Basket-relative 1M laggard'
            ELSE 'Mild laggard / watchlist'
        END AS laggard_methodology_flag,
        CASE
            WHEN rsi_14 < 35 AND ret_1m_z_score < -1 THEN 'Oversold mean reversion'
            WHEN production_factor_score >= basket_avg_factor_score AND ret_1m_pct < expected_ret_1m_pct THEN 'Factor-quality catch-up'
            WHEN size_z_score > 0 AND liquidity_z_score > 0 AND ret_1m_pct < expected_ret_1m_pct THEN 'Liquid large-cap catch-up'
            WHEN ret_3m_z_score < -1 AND ret_1m_z_score > ret_3m_z_score THEN 'Early recovery from 3M lag'
            ELSE 'Relative-value laggard'
        END AS long_thesis_type,
        CASE
            WHEN volatility_annual_polygon >= 0.60 THEN 'High volatility / lower sizing'
            WHEN ret_3m_z_score <= -2 THEN 'Possible value trap; deep persistent lag'
            WHEN rsi_14 > 70 THEN 'Already overbought despite lag score'
            WHEN short_pct_float >= 10 THEN 'Short-interest squeeze/fragility risk'
            ELSE 'Standard basket-relative risk'
        END AS risk_note,
        CASE
            WHEN rsi_14 < 35 THEN '1-4 weeks'
            WHEN ABS(regression_residual_1m_pct) >= 15 THEN '1-2 months'
            ELSE '2-4 months'
        END AS expected_time_horizon
    FROM scored
),
ranked AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY production_factor_basket
            ORDER BY relative_value_laggard_score DESC, regression_residual_1m_pct ASC, ret_1m_deviation_pct ASC
        ) AS laggard_rank_in_basket
    FROM laggard_scores
    WHERE basket_count >= 10
)
SELECT *
FROM ranked
ORDER BY production_factor_basket, laggard_rank_in_basket;

CREATE OR REPLACE VIEW v_vti_basket_relative_long_ideas_5b AS
SELECT
    production_factor_basket,
    laggard_rank_in_basket AS idea_rank_within_basket,
    ticker,
    company_name,
    sector,
    industry,
    market_cap,
    size_bucket,
    liquidity_bucket,
    production_theme,
    dolthub_overlay_label,
    clean_keyword_theme,
    primary_keyword_factor,
    ret_1m_pct,
    ret_3m_pct,
    ret_6m_pct,
    ret_ytd_pct,
    rsi_14,
    production_factor_score,
    production_factor_quintile,
    basket_count,
    basket_avg_ret_1m_pct,
    basket_avg_ret_3m_pct,
    basket_avg_factor_score,
    expected_ret_1m_pct,
    regression_residual_1m_pct,
    regression_residual_z_score,
    ret_1m_deviation_pct,
    ret_3m_deviation_pct,
    ret_1m_z_score,
    ret_3m_z_score,
    factor_score_z_score,
    size_z_score,
    liquidity_z_score,
    relative_value_laggard_score,
    laggard_methodology_flag,
    long_thesis_type,
    risk_note,
    expected_time_horizon,
    CASE
        WHEN long_thesis_type = 'Oversold mean reversion' THEN 'RSI and negative basket residual normalize'
        WHEN long_thesis_type = 'Factor-quality catch-up' THEN 'Price catches up to factor score within basket'
        WHEN long_thesis_type = 'Liquid large-cap catch-up' THEN 'Institutional rotation back into liquid laggard'
        WHEN long_thesis_type = 'Early recovery from 3M lag' THEN '1M improvement after 3M underperformance'
        ELSE 'Basket dispersion mean reverts'
    END AS expected_catalyst
FROM v_vti_basket_relative_laggards_5b
WHERE laggard_rank_in_basket <= 10
ORDER BY production_factor_basket, idea_rank_within_basket;

CREATE OR REPLACE VIEW v_vti_basket_relative_long_ideas_summary_5b AS
SELECT
    production_factor_basket,
    COUNT(*) AS idea_count,
    ROUND(AVG(relative_value_laggard_score), 3) AS avg_laggard_score,
    ROUND(AVG(regression_residual_1m_pct), 2) AS avg_regression_residual_1m_pct,
    ROUND(AVG(ret_1m_deviation_pct), 2) AS avg_ret_1m_deviation_pct,
    ROUND(AVG(ret_3m_deviation_pct), 2) AS avg_ret_3m_deviation_pct,
    ROUND(AVG(production_factor_score), 2) AS avg_production_factor_score,
    ROUND(AVG(market_cap) / 1000000000, 1) AS avg_market_cap_bn,
    STRING_AGG(ticker, ', ' ORDER BY idea_rank_within_basket) AS top_tickers
FROM v_vti_basket_relative_long_ideas_5b
GROUP BY production_factor_basket
ORDER BY avg_laggard_score DESC, avg_regression_residual_1m_pct ASC;
