# Polygon.io Stocks REST API — Data Dictionary

**Test Ticker:** AAPL (Apple Inc.)  
**Extraction Date:** 2026-05-15  
**Plan:** Stocks Starter ($29/mo)  
**Base URL:** `https://api.polygon.io`

## Summary

| Category | Endpoints | Plan Tier |
|----------|-----------|-----------|
| Tickers | 4 | 🟢 All Plans |
| Aggregate Bars (OHLC) | 4 | 🟢 All Plans |
| Snapshots | 3 | 🟡 Starter+ |
| Trades & Quotes | 2 | 🔴 Developer+ |
| Technical Indicators | 4 | 🟢 All Plans |
| Market Operations | 4 | 🟢 All Plans |
| Corporate Actions | 2 | 🟢 All Plans |
| News | 1 | 🟢 All Plans |
| Financials | 1 | 💰 Add-on (Starter) / Included (Dev+) |
| Short Data | 2 | 🟡 Starter+ |
| **Total** | **27** | **25 working, 2 blocked** |

**Total fields documented:** 442

> **Type Inference Note:** Large integers (>1B) are heuristically tagged `timestamp_s?` but may be dollar amounts (assets, revenue, etc.). Check the sample value to disambiguate. Fields tagged `string (datetime)` vs `string` may also have false positives on long text fields — verify against the sample value.

## Plan Availability Key

- 🟢 **All Plans** — Basic (Free), Starter, Developer, Advanced
- 🟡 **Starter+** — Starter ($29/mo) and above
- 🔴 **Developer+** — Developer ($79/mo) and above
- 💰 **Add-on** — Requires Financials & Ratios Expansion (+$29/mo) on Starter; included in Developer+

---

## All Tickers  `🟢 All Plans`

**Category:** Tickers
**Endpoint:** `GET /v3/reference/tickers`
**Status:** OK
**Results:** 1 items returned

| Field | Type | Nullable | Sample |
|-------|------|----------|--------|
| `status` | string (ticker/code) |  | OK |
| `request_id` | string |  | 13eaf592a7545cd5fa2dd1710a043a67 |
| `count` | integer |  | 1 |
| `results[].ticker` | string (ticker/code) |  | AAPL |
| `results[].name` | string |  | Apple Inc. |
| `results[].market` | string |  | stocks |
| `results[].locale` | string |  | us |
| `results[].primary_exchange` | string (ticker/code) |  | XNAS |
| `results[].type` | string (ticker/code) |  | CS |
| `results[].active` | boolean |  | True |
| `results[].currency_name` | string |  | usd |
| `results[].cik` | string |  | 0000320193 |
| `results[].composite_figi` | string |  | BBG000B9XRY4 |
| `results[].share_class_figi` | string |  | BBG001S5N8V8 |
| `results[].last_updated_utc` | string (datetime) |  | 2026-05-15T17:29:06.209036472Z |

---

## Ticker Overview  `🟢 All Plans`

**Category:** Tickers
**Endpoint:** `GET /v3/reference/tickers/{ticker}`
**Status:** OK

| Field | Type | Nullable | Sample |
|-------|------|----------|--------|
| `status` | string (ticker/code) |  | OK |
| `request_id` | string |  | 7e30feabcc124551ccf3481e2cb5edc1 |
| `results.ticker` | string (ticker/code) |  | AAPL |
| `results.name` | string |  | Apple Inc. |
| `results.market` | string |  | stocks |
| `results.locale` | string |  | us |
| `results.primary_exchange` | string (ticker/code) |  | XNAS |
| `results.type` | string (ticker/code) |  | CS |
| `results.active` | boolean |  | True |
| `results.currency_name` | string |  | usd |
| `results.cik` | string |  | 0000320193 |
| `results.composite_figi` | string |  | BBG000B9XRY4 |
| `results.share_class_figi` | string |  | BBG001S5N8V8 |
| `results.market_cap` | number |  | 4379916432759.9995 |
| `results.phone_number` | string |  | (408) 996-1010 |
| `results.address` | object |  | {...} |
| `results.address.address1` | string |  | ONE APPLE PARK WAY |
| `results.address.city` | string |  | CUPERTINO |
| `results.address.state` | string (ticker/code) |  | CA |
| `results.address.postal_code` | string |  | 95014 |
| `results.description` | string (datetime) |  | Apple is among the largest companies in the world, with a broad portfolio of hardware and software p |
| `results.sic_code` | string |  | 3571 |
| `results.sic_description` | string (datetime) |  | ELECTRONIC COMPUTERS |
| `results.ticker_root` | string (ticker/code) |  | AAPL |
| `results.homepage_url` | string (URL) |  | https://www.apple.com |
| `results.total_employees` | integer |  | 166000 |
| `results.list_date` | string (date) |  | 1980-12-12 |
| `results.branding` | object |  | {...} |
| `results.branding.logo_url` | string (URL) |  | https://api.polygon.io/v1/reference/company-branding/YXBwbGUuY29t/images/2025-04-04_logo.svg |
| `results.branding.icon_url` | string (URL) |  | https://api.polygon.io/v1/reference/company-branding/YXBwbGUuY29t/images/2025-04-04_icon.png |
| `results.share_class_shares_outstanding` | integer (timestamp_s?) |  | 14687356000 |
| `results.weighted_shares_outstanding` | integer (timestamp_s?) |  | 14687356000 |
| `results.round_lot` | integer |  | 40 |

---

## Ticker Types  `🟢 All Plans`

**Category:** Tickers
**Endpoint:** `GET /v3/reference/tickers/types`
**Status:** OK
**Results:** 25 items returned

| Field | Type | Nullable | Sample |
|-------|------|----------|--------|
| `status` | string (ticker/code) |  | OK |
| `request_id` | string |  | 52b58251c75c9b26dffc8c677cc8a809 |
| `count` | integer |  | 25 |
| `results[].code` | string (ticker/code) |  | CS |
| `results[].description` | string |  | Common Stock |
| `results[].asset_class` | string |  | stocks |
| `results[].locale` | string |  | us |

---

## Related Companies  `🟢 All Plans`

**Category:** Tickers
**Endpoint:** `GET /v1/related-companies/{ticker}`
**Status:** OK
**Results:** 10 items returned

| Field | Type | Nullable | Sample |
|-------|------|----------|--------|
| `status` | string (ticker/code) |  | OK |
| `request_id` | string |  | 000354918bf3cbd525a83ba9682c3866 |
| `results[].ticker` | string (ticker/code) |  | MSFT |

---

## Custom Bars  `🟢 All Plans`

**Category:** Aggregate Bars
**Endpoint:** `GET /v2/aggs/ticker/{ticker}/range/1/day/...`
**Status:** DELAYED
**Results:** 5 items returned

| Field | Type | Nullable | Sample |
|-------|------|----------|--------|
| `status` | string |  | DELAYED |
| `request_id` | string |  | 197b77c368b114f4a18ca30fc3a5e245 |
| `count` | integer |  | 5 |
| `next_url` | string (URL) |  | https://api.polygon.io/v2/aggs/ticker/AAPL/range/1/day/2026-05-08/1778471999999? |
| `results[].v` | number |  | 54863202.854363 |
| `results[].vw` | number |  | 300.4334 |
| `results[].o` | number |  | 297.9 |
| `results[].c` | number |  | 300.23 |
| `results[].h` | number |  | 303.2 |
| `results[].l` | number |  | 296.52 |
| `results[].t` | integer (timestamp_ms) |  | 1778817600000 |
| `results[].n` | integer |  | 777319 |

---

## Grouped Daily  `🟢 All Plans`

**Category:** Aggregate Bars
**Endpoint:** `GET /v2/aggs/grouped/locale/us/market/stocks/{date}`
**Status:** OK
**Results:** 12096 items returned

| Field | Type | Nullable | Sample |
|-------|------|----------|--------|
| `status` | string (ticker/code) |  | OK |
| `request_id` | string |  | df4d01a1e76da478167f36b81c492c5e |
| `count` | integer |  | 12096 |
| `results[].T` | string (ticker/code) |  | QQQS |
| `results[].v` | number |  | 5907.840528 |
| `results[].vw` | number |  | 41.4902 |
| `results[].o` | number |  | 42.18 |
| `results[].c` | number |  | 41.868 |
| `results[].h` | number |  | 42.18 |
| `results[].l` | number |  | 40.96 |
| `results[].t` | integer (timestamp_ms) |  | 1778616000000 |
| `results[].n` | integer |  | 145 |

---

## Open-Close  `🟢 All Plans`

**Category:** Aggregate Bars
**Endpoint:** `GET /v1/open-close/{ticker}/{date}`
**Status:** OK

| Field | Type | Nullable | Sample |
|-------|------|----------|--------|
| `status` | string (ticker/code) |  | OK |

---

## Previous Day  `🟢 All Plans`

**Category:** Aggregate Bars
**Endpoint:** `GET /v2/aggs/ticker/{ticker}/prev`
**Status:** OK
**Results:** 1 items returned

| Field | Type | Nullable | Sample |
|-------|------|----------|--------|
| `status` | string (ticker/code) |  | OK |
| `request_id` | string |  | 0c9bfebb8b4b0712e16d8f24f10a8b85 |
| `count` | integer |  | 1 |
| `results[].T` | string (ticker/code) |  | AAPL |
| `results[].v` | number |  | 54863202.0 |
| `results[].vw` | number |  | 300.4334 |
| `results[].o` | number |  | 297.9 |
| `results[].c` | number |  | 300.23 |
| `results[].h` | number |  | 303.2 |
| `results[].l` | number |  | 296.52 |
| `results[].t` | integer (timestamp_ms) |  | 1778875200000 |
| `results[].n` | integer |  | 777319 |

---

## Single Snapshot  `🟡 Starter+`

**Category:** Snapshots
**Endpoint:** `GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}`
**Status:** OK

| Field | Type | Nullable | Sample |
|-------|------|----------|--------|
| `status` | string (ticker/code) |  | OK |
| `request_id` | string |  | 9d67664d8e7baef9de5bbe707d075c1f |
| `ticker.ticker` | string (ticker/code) |  | AAPL |
| `ticker.todaysChangePerc` | number |  | 0.5486066865631684 |
| `ticker.todaysChange` | number |  | 1.636000000000024 |
| `ticker.updated` | integer (timestamp_ms) |  | 1778889600000000000 |
| `ticker.day` | object |  | {...} |
| `ticker.day.dv` | string |  | 54863202.854363 |
| `ticker.day.o` | number |  | 297.9 |
| `ticker.day.h` | number |  | 303.2 |
| `ticker.day.l` | number |  | 296.52 |
| `ticker.day.c` | number |  | 300.23 |
| `ticker.day.v` | number |  | 54863202.0 |
| `ticker.day.vw` | number |  | 300.4334 |
| `ticker.min` | object |  | {...} |
| `ticker.min.dv` | string |  | 1515.0 |
| `ticker.min.dav` | string |  | 54863202.854363 |
| `ticker.min.av` | integer (large) |  | 54863202 |
| `ticker.min.t` | integer (timestamp_ms) |  | 1778889540000 |
| `ticker.min.n` | integer |  | 22 |
| `ticker.min.o` | number |  | 299.77 |
| `ticker.min.h` | number |  | 299.8463 |
| `ticker.min.l` | number |  | 299.77 |
| `ticker.min.c` | number |  | 299.846 |
| `ticker.min.v` | integer |  | 1515 |
| `ticker.min.vw` | number |  | 299.7963 |
| `ticker.prevDay` | object |  | {...} |
| `ticker.prevDay.o` | number |  | 299.82 |
| `ticker.prevDay.h` | number |  | 300.45 |
| `ticker.prevDay.l` | number |  | 295.38 |
| `ticker.prevDay.c` | number |  | 298.21 |
| `ticker.prevDay.v` | number |  | 35324922.433075 |
| `ticker.prevDay.vw` | number |  | 298.2822 |

---

## Top Gainers  `🟡 Starter+`

**Category:** Snapshots
**Endpoint:** `GET /v2/snapshot/locale/us/markets/stocks/gainers`
**Status:** OK

| Field | Type | Nullable | Sample |
|-------|------|----------|--------|
| `status` | string (ticker/code) |  | OK |
| `request_id` | string |  | 0bf5fde078850e69090e35b202735695 |
| `tickers[].ticker` | string (ticker/code) |  | VIDA |
| `tickers[].todaysChangePerc` | number |  | 251999900.0 |
| `tickers[].todaysChange` | number |  | 2.519999 |
| `tickers[].updated` | integer (timestamp_ms) |  | 1778889600000000000 |
| `tickers[].day` | object |  | {...} |
| `tickers[].day.dv` | string |  | 1650930.876801 |
| `tickers[].day.o` | integer |  | 4 |
| `tickers[].day.h` | integer |  | 4 |
| `tickers[].day.l` | number |  | 2.5 |
| `tickers[].day.c` | number |  | 2.52 |
| `tickers[].day.v` | number |  | 1650930.0 |
| `tickers[].day.vw` | number |  | 3.4812 |
| `tickers[].min` | object |  | {...} |
| `tickers[].min.dv` | string |  | 285.0 |
| `tickers[].min.dav` | string |  | 1650930.876801 |
| `tickers[].min.av` | integer (large) |  | 1650930 |
| `tickers[].min.t` | integer (timestamp_ms) |  | 1778889540000 |
| `tickers[].min.n` | integer |  | 4 |
| `tickers[].min.o` | number |  | 2.62 |
| `tickers[].min.h` | number |  | 2.64 |
| `tickers[].min.l` | number |  | 2.62 |
| `tickers[].min.c` | number |  | 2.64 |
| `tickers[].min.v` | integer |  | 285 |
| `tickers[].min.vw` | number |  | 2.6315 |
| `tickers[].prevDay` | object |  | {...} |
| `tickers[].prevDay.o` | number |  | 1e-06 |
| `tickers[].prevDay.h` | number |  | 1e-06 |
| `tickers[].prevDay.l` | number |  | 1e-06 |
| `tickers[].prevDay.c` | number |  | 1e-06 |
| `tickers[].prevDay.v` | integer |  | 22500 |
| `tickers[].prevDay.vw` | number |  | 1e-06 |

---

## Top Losers  `🟡 Starter+`

**Category:** Snapshots
**Endpoint:** `GET /v2/snapshot/locale/us/markets/stocks/losers`
**Status:** OK

| Field | Type | Nullable | Sample |
|-------|------|----------|--------|
| `status` | string (ticker/code) |  | OK |
| `request_id` | string |  | ce129b33ef13a4bb5e52c3091a425cec |
| `tickers[].ticker` | string (ticker/code) |  | SOPA |
| `tickers[].todaysChangePerc` | number |  | -55.99563953488372 |
| `tickers[].todaysChange` | number |  | -0.15410000000000001 |
| `tickers[].updated` | integer (timestamp_ms) |  | 1778889600000000000 |
| `tickers[].day` | object |  | {...} |
| `tickers[].day.dv` | string |  | 16099459.734583 |
| `tickers[].day.o` | number |  | 0.1942 |
| `tickers[].day.h` | number |  | 0.2121 |
| `tickers[].day.l` | number |  | 0.1406 |
| `tickers[].day.c` | number |  | 0.1701 |
| `tickers[].day.v` | number |  | 16099459.0 |
| `tickers[].day.vw` | number |  | 0.2008 |
| `tickers[].min` | object |  | {...} |
| `tickers[].min.dv` | string |  | 137115.0 |
| `tickers[].min.dav` | string |  | 16099459.734583 |
| `tickers[].min.av` | integer (large) |  | 16099459 |
| `tickers[].min.t` | integer (timestamp_ms) |  | 1778889540000 |
| `tickers[].min.n` | integer |  | 42 |
| `tickers[].min.o` | number |  | 0.1445 |
| `tickers[].min.h` | number |  | 0.1445 |
| `tickers[].min.l` | number |  | 0.1211 |
| `tickers[].min.c` | number |  | 0.1211 |
| `tickers[].min.v` | integer |  | 137115 |
| `tickers[].min.vw` | number |  | 0.1331 |
| `tickers[].prevDay` | object |  | {...} |
| `tickers[].prevDay.o` | number |  | 0.26 |
| `tickers[].prevDay.h` | number |  | 0.2794 |
| `tickers[].prevDay.l` | number |  | 0.25 |
| `tickers[].prevDay.c` | number |  | 0.2752 |
| `tickers[].prevDay.v` | number |  | 2162079.333317 |
| `tickers[].prevDay.vw` | number |  | 0.2131 |

---

## Last Trade  `🔴 Developer+`

**Category:** Trades & Quotes
**Endpoint:** `GET /v2/last/trade/{ticker}`
**Status:** ERROR | **Error:** HTTP Error 403: Forbidden

> ⚠️ This endpoint returned: `HTTP Error 403: Forbidden`

---

## Last Quote  `🔴 Developer+`

**Category:** Trades & Quotes
**Endpoint:** `GET /v2/last/nbbo/{ticker}`
**Status:** ERROR | **Error:** HTTP Error 403: Forbidden

> ⚠️ This endpoint returned: `HTTP Error 403: Forbidden`

---

## SMA(50)  `🟢 All Plans`

**Category:** Technical Indicators
**Endpoint:** `GET /v1/indicators/sma/{ticker}`
**Status:** OK

| Field | Type | Nullable | Sample |
|-------|------|----------|--------|
| `status` | string (ticker/code) |  | OK |
| `request_id` | string |  | 2a9d8accd404c06681a290e280932088 |
| `next_url` | string (datetime) |  | https://api.polygon.io/v1/indicators/sma/AAPL?cursor=YWRqdXN0ZWQ9dHJ1ZSZhcD0lN0I |
| `results.underlying` | object |  | {...} |
| `results.underlying.url` | string (URL) |  | https://api.polygon.io/v2/aggs/ticker/AAPL/range/1/day/1063281600000/1778898823454?limit=228&sort=de |
| `results.values` | array[object] |  | [3 items] |
| `results.values[].timestamp` | integer (timestamp_ms) |  | 1778817600000 |
| `results.values[].value` | number |  | 266.18919999999997 |

---

## EMA(20)  `🟢 All Plans`

**Category:** Technical Indicators
**Endpoint:** `GET /v1/indicators/ema/{ticker}`
**Status:** OK

| Field | Type | Nullable | Sample |
|-------|------|----------|--------|
| `status` | string (ticker/code) |  | OK |
| `request_id` | string |  | 71f9e2cf0b4d88087d16eb3fdd533ef6 |
| `next_url` | string (datetime) |  | https://api.polygon.io/v1/indicators/ema/AAPL?cursor=YWRqdXN0ZWQ9dHJ1ZSZhcD0lN0I |
| `results.underlying` | object |  | {...} |
| `results.underlying.url` | string (URL) |  | https://api.polygon.io/v2/aggs/ticker/AAPL/range/1/day/1063281600000/1778898823504?limit=95&sort=des |
| `results.values` | array[object] |  | [3 items] |
| `results.values[].timestamp` | integer (timestamp_ms) |  | 1778817600000 |
| `results.values[].value` | number |  | 283.75446768093695 |

---

## RSI(14)  `🟢 All Plans`

**Category:** Technical Indicators
**Endpoint:** `GET /v1/indicators/rsi/{ticker}`
**Status:** OK

| Field | Type | Nullable | Sample |
|-------|------|----------|--------|
| `status` | string (ticker/code) |  | OK |
| `request_id` | string |  | c5483b9b81bf5e4c6ad398d3b79e9b4e |
| `next_url` | string (datetime) |  | https://api.polygon.io/v1/indicators/rsi/AAPL?cursor=YWRqdXN0ZWQ9dHJ1ZSZhcD0lN0I |
| `results.underlying` | object |  | {...} |
| `results.underlying.url` | string (URL) |  | https://api.polygon.io/v2/aggs/ticker/AAPL/range/1/day/1063281600000/1778898823550?limit=68&sort=des |
| `results.values` | array[object] |  | [3 items] |
| `results.values[].timestamp` | integer (timestamp_ms) |  | 1778817600000 |
| `results.values[].value` | number |  | 75.43355811534856 |

---

## MACD  `🟢 All Plans`

**Category:** Technical Indicators
**Endpoint:** `GET /v1/indicators/macd/{ticker}`
**Status:** OK

| Field | Type | Nullable | Sample |
|-------|------|----------|--------|
| `status` | string (ticker/code) |  | OK |
| `request_id` | string |  | da6ad087d5f4a76af80b220898656a49 |
| `next_url` | string (datetime) |  | https://api.polygon.io/v1/indicators/macd/AAPL?cursor=YWRqdXN0ZWQ9dHJ1ZSZhcD0lN0 |
| `results.underlying` | object |  | {...} |
| `results.underlying.url` | string (URL) |  | https://api.polygon.io/v2/aggs/ticker/AAPL/range/1/day/1063281600000/1778898823589?limit=122&sort=de |
| `results.values` | array[object] |  | [3 items] |
| `results.values[].timestamp` | integer (timestamp_ms) |  | 1778817600000 |
| `results.values[].value` | number |  | 9.456764605132037 |
| `results.values[].signal` | number |  | 7.71049828209892 |
| `results.values[].histogram` | number |  | 1.746266323033117 |

---

## Exchanges  `🟢 All Plans`

**Category:** Market Operations
**Endpoint:** `GET /v3/reference/exchanges`
**Status:** OK
**Results:** 52 items returned

| Field | Type | Nullable | Sample |
|-------|------|----------|--------|
| `status` | string (ticker/code) |  | OK |
| `request_id` | string |  | 8906b1be8f36d3a2b34791e973c3441b |
| `count` | integer |  | 52 |
| `results[].id` | integer |  | 1 |
| `results[].type` | string |  | exchange |
| `results[].asset_class` | string |  | stocks |
| `results[].locale` | string |  | us |
| `results[].name` | string |  | NYSE American, LLC |
| `results[].acronym` | string (ticker/code) |  | AMEX |
| `results[].mic` | string (ticker/code) |  | XASE |
| `results[].operating_mic` | string (ticker/code) |  | XNYS |
| `results[].participant_id` | string (ticker/code) |  | A |
| `results[].url` | string (URL) |  | https://www.nyse.com/markets/nyse-american |

---

## Market Holidays  `🟢 All Plans`

**Category:** Market Operations
**Endpoint:** `GET /v1/marketstatus/upcoming`
**Status:** OK (list)
**Results:** 24 items returned

| Field | Type | Nullable | Sample |
|-------|------|----------|--------|
| `date` | string (date) |  | 2026-05-25 |
| `exchange` | string (ticker/code) |  | NYSE |
| `name` | string |  | Memorial Day |
| `status` | string |  | closed |

---

## Market Status  `🟢 All Plans`

**Category:** Market Operations
**Endpoint:** `GET /v1/marketstatus/now`
**Status:** ?

| Field | Type | Nullable | Sample |
|-------|------|----------|--------|
| `currencies.crypto` | string |  | open |
| `currencies.fx` | string |  | closed |
| `exchanges.nasdaq` | string |  | closed |
| `exchanges.nyse` | string |  | closed |
| `exchanges.otc` | string |  | closed |
| `indicesGroups.s_and_p` | string |  | closed |
| `indicesGroups.societe_generale` | string |  | closed |
| `indicesGroups.msci` | string |  | closed |
| `indicesGroups.ftse_russell` | string |  | closed |
| `indicesGroups.mstar` | string |  | open |
| `indicesGroups.mstarc` | string |  | open |
| `indicesGroups.cccy` | string |  | open |
| `indicesGroups.cgi` | string |  | closed |
| `indicesGroups.nasdaq` | string |  | closed |
| `indicesGroups.dow_jones` | string |  | closed |

---

## Condition Codes  `🟢 All Plans`

**Category:** Market Operations
**Endpoint:** `GET /v3/reference/conditions`
**Status:** OK
**Results:** 5 items returned

| Field | Type | Nullable | Sample |
|-------|------|----------|--------|
| `status` | string (ticker/code) |  | OK |
| `request_id` | string |  | 43d6e5d28230afe8747262fe8c254d7d |
| `count` | integer |  | 5 |
| `next_url` | string (URL) |  | https://api.polygon.io/v3/reference/conditions?cursor=YXA9NSZhcz0mbGltaXQ9NSZzb3 |
| `results[].id` | integer |  | 0 |
| `results[].type` | string |  | regular |
| `results[].name` | string |  | Regular Trade |
| `results[].asset_class` | string |  | crypto |
| `results[].data_types` | array |  | ['trade'] |

---

## Dividends  `🟢 All Plans`

**Category:** Corporate Actions
**Endpoint:** `GET /v3/reference/dividends`
**Status:** OK
**Results:** 3 items returned

| Field | Type | Nullable | Sample |
|-------|------|----------|--------|
| `status` | string (ticker/code) |  | OK |
| `request_id` | string |  | e0fd9c73435e1b89ac366d7bd0e848e9 |
| `next_url` | string (datetime) |  | https://api.polygon.io/v3/reference/dividends?cursor=YXA9MyZhcz0mbGltaXQ9MyZvcmR |
| `results[].cash_amount` | number |  | 0.27 |
| `results[].currency` | string (ticker/code) |  | USD |
| `results[].declaration_date` | string (date) |  | 2026-04-30 |
| `results[].dividend_type` | string (ticker/code) |  | CD |
| `results[].ex_dividend_date` | string (date) |  | 2026-05-11 |
| `results[].frequency` | integer |  | 4 |
| `results[].id` | string |  | E9fd05fa01d55de07885332c97a263e93ac4bf03c742faef9b4f18544b34e928f |
| `results[].pay_date` | string (date) |  | 2026-05-14 |
| `results[].record_date` | string (date) |  | 2026-05-11 |
| `results[].ticker` | string (ticker/code) |  | AAPL |

---

## Stock Splits  `🟢 All Plans`

**Category:** Corporate Actions
**Endpoint:** `GET /v3/reference/splits`
**Status:** OK
**Results:** 3 items returned

| Field | Type | Nullable | Sample |
|-------|------|----------|--------|
| `status` | string (ticker/code) |  | OK |
| `request_id` | string |  | 62170261b417dc2763d6ac2e5bb982a4 |
| `next_url` | string (URL) |  | https://api.polygon.io/v3/reference/splits?cursor=YXA9MyZhcz0mbGltaXQ9MyZvcmRlcj |
| `results[].execution_date` | string (date) |  | 2020-08-31 |
| `results[].id` | string |  | E36416cce743c3964c5da63e1ef1626c0aece30fb47302eea5a49c0055c04e8d0 |
| `results[].split_from` | integer |  | 1 |
| `results[].split_to` | integer |  | 4 |
| `results[].ticker` | string (ticker/code) |  | AAPL |

---

## Ticker News  `🟢 All Plans`

**Category:** News
**Endpoint:** `GET /v2/reference/news`
**Status:** OK
**Results:** 3 items returned

| Field | Type | Nullable | Sample |
|-------|------|----------|--------|
| `status` | string (ticker/code) |  | OK |
| `request_id` | string |  | cd2eb5a006e0212dd42d3bc58aebb102 |
| `count` | integer |  | 3 |
| `next_url` | string (datetime) |  | https://api.polygon.io/v2/reference/news?cursor=YXA9MjAyNi0wNS0xNVQxNCUzQTEzJTNB |
| `results[].id` | string |  | 77ab735d6e941791c2674d8b79e53c4296b385530de20912820ec416cf84ed23 |
| `results[].publisher` | object |  | {...} |
| `results[].publisher.name` | string |  | Benzinga |
| `results[].publisher.homepage_url` | string (URL) |  | https://www.benzinga.com/ |
| `results[].publisher.logo_url` | string (URL) |  | https://s3.polygon.io/public/assets/news/logos/benzinga.svg |
| `results[].publisher.favicon_url` | string (URL) |  | https://s3.polygon.io/public/assets/news/favicons/benzinga.ico |
| `results[].title` | string (datetime) |  | Al Rally Hits Rate-Hike Wall As Inflation Spikes: This Week On Wall Street |
| `results[].author` | string |  | Piero Cingari |
| `results[].published_utc` | string (datetime) |  | 2026-05-15T20:01:09Z |
| `results[].article_url` | string (URL) |  | https://www.benzinga.com/markets/equities/26/05/52614590/ai-rally-rate-hike-expectations-inflation-b |
| `results[].tickers` | array |  | ['F', 'FpB', 'FpC', 'FpD', 'AAPL', 'TSLA', 'NVDA'] |
| `results[].image_url` | string (URL) |  | https://cdn.benzinga.com/files/images/story/2026/05/15/inflation-ai2.png?width=1200&height=800&fit=c |
| `results[].description` | string (datetime) |  | The AI-driven market rally faces headwinds as April consumer inflation surged to 3.8% and producer i |
| `results[].keywords` | array |  | ['inflation', 'Federal Reserve', 'rate hike', 'AI rally', 'bond market', 'Ford Energy', 'CPI', 'PPI' |
| `results[].insights` | array[object] |  | [7 items] |
| `results[].insights[].ticker` | string (ticker/code) |  | F |
| `results[].insights[].sentiment` | string |  | positive |
| `results[].insights[].sentiment_reasoning` | string (datetime) |  | Ford shares surged 13.2% on Wednesday and added 6.7% Thursday following Morgan Stanley's bullish not |

---

## Financials  `💰 Add-on on Starter (included Dev+)`

**Category:** Financials
**Endpoint:** `GET /v2/reference/financials/{ticker}`
**Status:** OK
**Results:** 3 items returned

| Field | Type | Nullable | Sample |
|-------|------|----------|--------|
| `status` | string (ticker/code) |  | OK |
| `results[].ticker` | string (ticker/code) |  | AAPL |
| `results[].period` | string (ticker/code) |  | QA |
| `results[].calendarDate` | string (date) |  | 2020-03-31 |
| `results[].reportPeriod` | string (date) |  | 2020-03-28 |
| `results[].updated` | string (date) |  | 2020-05-01 |
| `results[].dateKey` | string (date) |  | 2020-03-28 |
| `results[].accumulatedOtherComprehensiveIncome` | integer |  | -2789000000 |
| `results[].assets` | integer (timestamp_s?) |  | 320400000000 |
| `results[].assetsCurrent` | integer (timestamp_s?) |  | 143753000000 |
| `results[].assetsNonCurrent` | integer (timestamp_s?) |  | 176647000000 |
| `results[].bookValuePerShare` | number |  | 17.987 |
| `results[].capitalExpenditure` | integer |  | -1853000000 |
| `results[].cashAndEquivalents` | integer (timestamp_s?) |  | 40174000000 |
| `results[].cashAndEquivalentsUSD` | integer (timestamp_s?) |  | 40174000000 |
| `results[].costOfRevenue` | integer (timestamp_s?) |  | 35943000000 |
| `results[].consolidatedIncome` | integer (timestamp_s?) |  | 11249000000 |
| `results[].currentRatio` | number |  | 1.496 |
| `results[].debtToEquityRatio` | number |  | 3.085 |
| `results[].debt` | integer (timestamp_s?) |  | 109507000000 |
| `results[].debtCurrent` | integer (timestamp_s?) |  | 20421000000 |
| `results[].debtNonCurrent` | integer (timestamp_s?) |  | 89086000000 |
| `results[].debtUSD` | integer (timestamp_s?) |  | 109507000000 |
| `results[].deferredRevenue` | integer (timestamp_s?) |  | 5928000000 |
| `results[].depreciationAmortizationAndAccretion` | integer (timestamp_s?) |  | 2786000000 |
| `results[].deposits` | integer |  | 0 |
| `results[].dividendYield` | number |  | 0.012 |
| `results[].dividendsPerBasicCommonShare` | number |  | 0.77 |
| `results[].earningBeforeInterestTaxes` | integer (timestamp_s?) |  | 13135000000 |
| `results[].earningsBeforeInterestTaxesDepreciationAmortization` | integer (timestamp_s?) |  | 15921000000 |
| `results[].EBITDAMargin` | number |  | 0.273 |
| `results[].earningsBeforeInterestTaxesDepreciationAmortizationUSD` | integer (timestamp_s?) |  | 15921000000 |
| `results[].earningBeforeInterestTaxesUSD` | integer (timestamp_s?) |  | 13135000000 |
| `results[].earningsBeforeTax` | integer (timestamp_s?) |  | 13135000000 |
| `results[].earningsPerBasicShare` | number |  | 2.58 |
| `results[].earningsPerDilutedShare` | number |  | 2.55 |
| `results[].earningsPerBasicShareUSD` | number |  | 2.58 |
| `results[].shareholdersEquity` | integer (timestamp_s?) |  | 78425000000 |
| `results[].shareholdersEquityUSD` | integer (timestamp_s?) |  | 78425000000 |
| `results[].enterpriseValue` | integer (timestamp_ms) |  | 1152502415200 |
| `results[].enterpriseValueOverEBIT` | integer |  | 17 |
| `results[].enterpriseValueOverEBITDA` | number |  | 14.625 |
| `results[].freeCashFlow` | integer (timestamp_s?) |  | 11458000000 |
| `results[].freeCashFlowPerShare` | number |  | 2.628 |
| `results[].foreignCurrencyUSDExchangeRate` | integer |  | 1 |
| `results[].grossProfit` | integer (timestamp_s?) |  | 22370000000 |
| `results[].grossMargin` | number |  | 0.384 |
| `results[].goodwillAndIntangibleAssets` | integer |  | 0 |
| `results[].interestExpense` | integer |  | 0 |
| `results[].investedCapital` | integer (timestamp_s?) |  | 293639000000 |
| `results[].inventory` | integer (timestamp_s?) |  | 3334000000 |
| `results[].investments` | integer (timestamp_s?) |  | 152670000000 |
| `results[].investmentsCurrent` | integer (timestamp_s?) |  | 53877000000 |
| `results[].investmentsNonCurrent` | integer (timestamp_s?) |  | 98793000000 |
| `results[].totalLiabilities` | integer (timestamp_s?) |  | 241975000000 |
| `results[].currentLiabilities` | integer (timestamp_s?) |  | 96094000000 |
| `results[].liabilitiesNonCurrent` | integer (timestamp_s?) |  | 145881000000 |
| `results[].marketCapitalization` | integer (timestamp_ms) |  | 1083981415200 |
| `results[].netCashFlow` | integer (timestamp_s?) |  | 1384000000 |
| `results[].netCashFlowBusinessAcquisitionsDisposals` | integer |  | -176000000 |
| `results[].issuanceEquityShares` | integer |  | -18146000000 |
| `results[].issuanceDebtSecurities` | integer (large) |  | 803000000 |
| `results[].paymentDividendsOtherCashDistributions` | integer |  | -3375000000 |
| `results[].netCashFlowFromFinancing` | integer |  | -20940000000 |
| `results[].netCashFlowFromInvesting` | integer (timestamp_s?) |  | 9013000000 |
| `results[].netCashFlowInvestmentAcquisitionsDisposals` | integer (timestamp_s?) |  | 11338000000 |
| `results[].netCashFlowFromOperations` | integer (timestamp_s?) |  | 13311000000 |
| `results[].effectOfExchangeRateChangesOnCash` | integer |  | 0 |
| `results[].netIncome` | integer (timestamp_s?) |  | 11249000000 |
| `results[].netIncomeCommonStock` | integer (timestamp_s?) |  | 11249000000 |
| `results[].netIncomeCommonStockUSD` | integer (timestamp_s?) |  | 11249000000 |
| `results[].netLossIncomeFromDiscontinuedOperations` | integer |  | 0 |
| `results[].netIncomeToNonControllingInterests` | integer |  | 0 |
| `results[].profitMargin` | number |  | 0.193 |
| `results[].operatingExpenses` | integer (timestamp_s?) |  | 9517000000 |
| `results[].operatingIncome` | integer (timestamp_s?) |  | 12853000000 |
| `results[].tradeAndNonTradePayables` | integer (timestamp_s?) |  | 32421000000 |
| `results[].payoutRatio` | number |  | 0.298 |
| `results[].priceToBookValue` | number |  | 13.822 |
| `results[].priceEarnings` | number |  | 18.946 |
| `results[].priceToEarningsRatio` | number |  | 19.249 |
| `results[].propertyPlantEquipmentNet` | integer (timestamp_s?) |  | 35889000000 |
| `results[].preferredDividendsIncomeStatementImpact` | integer |  | 0 |
| `results[].sharePriceAdjustedClose` | number |  | 247.74 |
| `results[].priceSales` | number |  | 4.045 |
| `results[].priceToSalesRatio` | number |  | 4.031 |
| `results[].tradeAndNonTradeReceivables` | integer (timestamp_s?) |  | 30677000000 |
| `results[].accumulatedRetainedEarningsDeficit` | integer (timestamp_s?) |  | 33182000000 |
| `results[].revenues` | integer (timestamp_s?) |  | 58313000000 |
| `results[].revenuesUSD` | integer (timestamp_s?) |  | 58313000000 |
| `results[].researchAndDevelopmentExpense` | integer (timestamp_s?) |  | 4565000000 |
| `results[].shareBasedCompensation` | integer (timestamp_s?) |  | 1697000000 |
| `results[].sellingGeneralAndAdministrativeExpense` | integer (timestamp_s?) |  | 4952000000 |
| `results[].shareFactor` | integer |  | 1 |
| `results[].shares` | integer (timestamp_s?) |  | 4375480000 |
| `results[].weightedAverageShares` | integer (timestamp_s?) |  | 4360101000 |
| `results[].weightedAverageSharesDiluted` | integer (timestamp_s?) |  | 4404691000 |
| `results[].salesPerShare` | number |  | 13.374 |
| `results[].tangibleAssetValue` | integer (timestamp_s?) |  | 320400000000 |
| `results[].taxAssets` | integer |  | 0 |
| `results[].incomeTaxExpense` | integer (timestamp_s?) |  | 1886000000 |
| `results[].taxLiabilities` | integer |  | 0 |
| `results[].tangibleAssetsBookValuePerShare` | number |  | 73.485 |
| `results[].workingCapital` | integer (timestamp_s?) |  | 47659000000 |

---

## Short Interest  `🟡 Starter+`

**Category:** Short Data
**Endpoint:** `GET /stocks/v1/short-interest`
**Status:** OK
**Results:** 3 items returned

| Field | Type | Nullable | Sample |
|-------|------|----------|--------|
| `status` | string (ticker/code) |  | OK |
| `request_id` | string |  | 80234d614cb7424eb40bb4b8723be86d |
| `next_url` | string (URL) |  | https://api.polygon.io/stocks/v1/short-interest?cursor=ARIPBEFBUEwBAAAAAQMAAQEPC |
| `results[].settlement_date` | string (date) |  | 2026-04-30 |
| `results[].ticker` | string (ticker/code) |  | AAPL |
| `results[].short_interest` | integer (large) |  | 134675274 |
| `results[].avg_daily_volume` | integer (large) |  | 45944025 |
| `results[].days_to_cover` | number |  | 2.93 |

---

## Short Volume  `🟡 Starter+`

**Category:** Short Data
**Endpoint:** `GET /stocks/v1/short-volume`
**Status:** OK
**Results:** 3 items returned

| Field | Type | Nullable | Sample |
|-------|------|----------|--------|
| `status` | string (ticker/code) |  | OK |
| `request_id` | string |  | 55000d6cf0454953b9025a5e9339c931 |
| `next_url` | string (datetime) |  | https://api.polygon.io/stocks/v1/short-volume?cursor=AQwPBEFBUEwCAAABAQABAQMAAgE |
| `results[].ticker` | string (ticker/code) |  | AAPL |
| `results[].date` | string (date) |  | 2024-02-06 |
| `results[].total_volume` | number |  | 16264662.0 |
| `results[].short_volume` | number |  | 5683713.0 |
| `results[].exempt_volume` | number |  | 67840.0 |
| `results[].non_exempt_volume` | number |  | 5615873.0 |
| `results[].short_volume_ratio` | number |  | 34.95 |
| `results[].nyse_short_volume` | integer |  | 356029 |
| `results[].nyse_short_volume_exempt` | integer |  | 4308 |
| `results[].nasdaq_carteret_short_volume` | integer (large) |  | 5298900 |
| `results[].nasdaq_carteret_short_volume_exempt` | integer |  | 63532 |
| `results[].nasdaq_chicago_short_volume` | integer |  | 28784 |
| `results[].nasdaq_chicago_short_volume_exempt` | integer |  | 0 |
| `results[].adf_short_volume` | integer |  | 0 |
| `results[].adf_short_volume_exempt` | integer |  | 0 |

---

## WebSocket Channels  `🟡 Starter+ (delayed) / 🔴 Advanced (real-time)`

**Endpoint:** `wss://delayed.massive.com/stocks` (Starter) / `wss://socket.massive.com/stocks` (Advanced)
**Auth:** API key as `action: "auth"` param
**Protocol:** JSON messages, subscribe via `action: "subscribe"`

| Channel | Code | Description | Plan Availability |
|---------|------|-------------|-------------------|
| Per-second aggregates | `A` | 1-second OHLCV bars per ticker | 🟡 Starter+ |
| Per-minute aggregates | `AM` | 1-minute OHLCV bars per ticker | 🟡 Starter+ |
| Tick-level trades | `T` | Individual trade executions (price, size, exchange, conditions) | 🔴 Developer+ |
| NBBO quotes | `Q` | National best bid/offer with size and exchange | 🔴 Developer+ |
| LULD events | `LULD` | Limit Up-Limit Down trading halt notifications | 🟡 Starter+ |
| Net Order Imbalance | `NOI` | Order imbalance data (needs Imbalances add-on on Starter) | 🟡 Starter+ with add-on |

**Message fields** (per-second and per-minute aggregates):

| Field | Type | Description |
|-------|------|-------------|
| `ev` | string | Event type: `"A"` (per-second) or `"AM"` (per-minute) |
| `sym` | string | Ticker symbol (e.g., `"AAPL"`) |
| `v` | number | Tick volume (for this bar) |
| `av` | integer | Accumulated volume (total shares traded today) |
| `op` | number | Tick open price (for this bar) |
| `vw` | number | Volume-weighted average price |
| `o` | number | Bar open price |
| `c` | number | Bar close price |
| `h` | number | Bar high price |
| `l` | number | Bar low price |
| `a` | number | Accumulated VWAP |
| `s` | integer | Bar start timestamp (Unix ms) |
| `e` | integer | Bar end timestamp (Unix ms) |

**Connection flow:**
1. Connect to `wss://delayed.massive.com/stocks`
2. Authenticate: `{"action": "auth", "params": "<API_KEY>"}`
3. Wait for `{"status": "auth_success"}` response
4. Subscribe: `{"action": "subscribe", "params": "AM.AAPL,MSFT"}` (channel prefix + comma-separated tickers)
5. Stream begins — parse each JSON message

**Latency:** 15-minute delayed on Starter plan; real-time on Advanced plan.

---

## Volume Profile Data Shape

**Method:** `PolygonClient.volume_profile(ticker, days=20, bin_minutes=5, timespan="minute")`

Returns a dict with time-aligned accumulated volume analysis across N trading days.

### Top-level return structure

| Key | Type | Description |
|-----|------|-------------|
| `status` | string | `"OK"` or `"ERROR"` |
| `ticker` | string | Uppercased ticker symbol (e.g., `"AAPL"`) |
| `days_sampled` | integer | Number of trading days with data |
| `total_bars` | integer | Total minute bars pulled across all days |
| `bin_minutes` | integer | Time bin size in minutes (default: 5) |
| `timezone` | string | `"ET"` (Eastern Time) |
| `snapshots` | array[object] | Key intraday milestones with cumulative volume stats |
| `profile` | object | Full time-aligned profile keyed by minute-of-day (ET) |

### `snapshots[]` object fields

Each snapshot is a key intraday time point (9:30, 10:30, 12:00, 13:00, 14:00, 15:00, 15:30 ET):

| Field | Type | Description |
|-------|------|-------------|
| `time` | string | ET time label (e.g., `"09:30"`, `"12:00"`) |
| `avg_cum_vol` | integer | Average cumulative volume at this minute across all days |
| `std` | integer | Standard deviation of cumulative volume |
| `cv_pct` | number | Coefficient of variation as percentage (std/avg × 100) |
| `above_15x` | integer | 1.5× the average — threshold for "high volume" signal |
| `below_05x` | integer | 0.5× the average — threshold for "low volume" signal |

### `profile` object

Keyed by minute-of-day in ET (e.g., `570` = 9:30 AM, `930` = 3:30 PM).
Grouped into bins of `bin_minutes` (default 5 minutes).

Each value is an object:

| Field | Type | Description |
|-------|------|-------------|
| `avg` | integer | Average cumulative volume across all sampled days |
| `std` | integer | Standard deviation of cumulative volume |
| `cv_pct` | number | Coefficient of variation (%) — measures day-to-day consistency |
| `p25` | integer | 25th percentile cumulative volume |
| `p75` | integer | 75th percentile cumulative volume |
| `count` | integer | Number of days contributing to this bin |
| `above_15x` | integer | 1.5× avg threshold (upper signal band) |
| `below_05x` | integer | 0.5× avg threshold (lower signal band) |

**Example usage:**

```python
client = PolygonClient()
profile = client.volume_profile("AAPL", days=20)

# Check snapshots at key times
for snap in profile["snapshots"]:
    print(f"{snap['time']}: avg={snap['avg_cum_vol']:,} CV={snap['cv_pct']:.1f}%")

# Access full profile
for minute_et, stats in sorted(profile["profile"].items()):
    print(f"Minute {minute_et}: avg={stats['avg']:,} cv={stats['cv_pct']:.1f}%")
```

**Edge cases:**
- If fewer than 3 days have data for a time bin, that bin is excluded from the profile
- If the intraday bars request fails, returns `{"status": "ERROR", "error": "..."}`
- Times before 4:00 AM ET (minute_bin < 240) are excluded to avoid pre-market/pre-market noise

