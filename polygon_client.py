#!/usr/bin/env python3
"""
Polygon.io Stocks REST + WebSocket API — Modular Client v5
============================================================
Zero-dependency (stdlib + websocket-client for streaming).
Plan-aware: defaults to Starter ($29/mo). Rate-limited, paginated,
retry-resilient, cached. Covers all working endpoints + WebSocket.

Plan Capabilities:
    Starter ($29/mo):  REST: reference, OHLCV, technicals, snapshots,
                       corp actions, news+sentiment, short data, flat files.
                       WS: 15-min delayed — per-second (A), per-minute (AM),
                       trades (T), quotes (Q), LULD, NOI.
    + $29/mo add-on:   Current-quarter financials (balance sheet, P&L, ratios).
    + $99/mo Benzinga: Analyst ratings, earnings events, bull/bear, premium news.

Key Learnings (May 2026 — v5 consolidation):
    1. Minute bars ARE available historically via REST (5 years on Starter).
    2. Volume distribution is U-shaped — open/close peak, midday trough.
    3. Time-aligned accumulated volume: CV drops 60%→27% through the day.
    4. WebSocket on Starter: wss://delayed.massive.com/stocks (15-min delayed).
    5. Financials data on Starter ends at 2020-03-31 without add-on.
    6. Sentiment insights embedded in standard news — no Benzinga needed.
    7. DELAYED status is NOT an error — _ok() accepts both "OK" and "DELAYED".
    8. Snapshot response format is non-standard: {ticker: {day, min, prevDay}},
       NOT {results: [...]}. Gainers/losers use {tickers: [...]}.
    9. Short interest endpoint: /stocks/v1/short-interest — NOT /v1/indicators/.
   10. Financials API returns oldest-first → client auto-sorts descending.
   11. as_completed() timeout is MANDATORY in bulk fetches — without it,
       a single stuck call freezes the entire ThreadPoolExecutor.
   12. pool.map() blocks silently for minutes on large chunks — use
       as_completed() with per-ticker timeout instead.

Usage:
    client = PolygonClient()                              # auto: Starter plan
    client = PolygonClient(plan="starter")
    client.capabilities.available                         # list available features
    client.can("websocket")                               # True

    # Volume Profile
    profile = client.volume_profile("AAPL", days=20)      # time-aligned cum vol
    snap = client.volume_screener_snapshot("AAPL")        # today vs 20d avg

    # WebSocket (15-min delayed on Starter)
    ws = client.websocket_stream(["AAPL", "MSFT"])        # connect + subscribe
    for bar in ws:                                        # iterate bars
        if bar["ev"] == "AM": print(bar["sym"], bar["av"])
"""
from __future__ import annotations

import os, json, time, urllib.request, urllib.parse, threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Callable, Iterator
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError


# ═══════════════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════════════

def _load_api_key() -> str:
    key = os.environ.get("POLYGON_API_KEY")
    if key: return key
    env_file = Path.home() / ".hermes" / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("POLYGON_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("POLYGON_API_KEY not found in env or ~/.hermes/.env")

POLYGON_PLAN = os.environ.get("POLYGON_PLAN", "starter").lower()
DEFAULT_MAX_WORKERS = 8

PLAN_LIMITS: dict[str, tuple[int, float]] = {
    "free": (5, 60.0), "basic": (5, 60.0),
    # All paid plans have UNLIMITED API calls per Polygon pricing page.
    # (0, 1.0) = unlimited mode with 1ms polite floor between calls.
    "starter": (0, 1.0), "developer": (0, 1.0), "advanced": (0, 1.0),
    "unlimited": (0, 1.0),
}

# ═══════════════════════════════════════════════════════════════════════
# Plan Capabilities
# ═══════════════════════════════════════════════════════════════════════

class Capabilities:
    """What's available on your plan — and what requires an upgrade."""

    PLAN_FEATURES = {
        "free": {"reference", "ohlcv", "technicals", "corp_actions", "news", "flat_files"},
        "starter": {"reference", "ohlcv", "technicals", "corp_actions", "news", "sentiment",
                     "snapshots", "short_interest", "short_volume", "flat_files",
                     "websocket", "intraday_bars"},
        "developer": {"reference", "ohlcv", "technicals", "corp_actions", "news", "sentiment",
                       "snapshots", "short_interest", "short_volume", "trades", "flat_files",
                       "websocket", "intraday_bars"},
        "advanced": {"reference", "ohlcv", "technicals", "corp_actions", "news", "sentiment",
                      "snapshots", "short_interest", "short_volume", "trades", "quotes",
                      "financials", "flat_files", "websocket", "intraday_bars"},
    }

    ADDONS = {
        "financials_addon": {"price": "$29/mo", "plans": ["starter"],
                             "desc": "Current-quarter fundamentals (balance sheet, P&L, ratios)"},
        "benzinga_ratings":  {"price": "$99/mo", "plans": ["starter", "developer", "advanced"],
                              "desc": "Analyst upgrades/downgrades, price targets, firm/analyst names"},
        "benzinga_earnings": {"price": "$99/mo", "plans": ["starter", "developer", "advanced"],
                              "desc": "EPS/revenue actuals vs estimates, surprise %, historical"},
        "benzinga_bulls_bears": {"price": "$99/mo", "plans": ["starter", "developer", "advanced"],
                                 "desc": "Concise bull and bear case summaries per ticker"},
        "benzinga_news": {"price": "$99/mo", "plans": ["starter", "developer", "advanced"],
                          "desc": "Premium Benzinga articles with teasers, channels, rich metadata"},
        "benzinga_insights": {"price": "$99/mo", "plans": ["starter", "developer", "advanced"],
                              "desc": "Analyst commentary explaining ratings and forecasts"},
        "benzinga_guidance": {"price": "$99/mo", "plans": ["starter", "developer", "advanced"],
                              "desc": "Company-issued forward-looking forecasts"},
    }

    WS_CHANNELS = {
        "A":   "Per-second aggregates (OHLCV)",
        "AM":  "Per-minute aggregates (OHLCV)",
        "T":   "Tick-level trades",
        "Q":   "NBBO quotes (bid/ask)",
        "LULD":"Limit Up-Limit Down events",
        "NOI": "Net Order Imbalance (needs Imbalances add-on on Starter)",
    }

    WS_DELAYED_URL = "wss://delayed.massive.com/stocks"
    WS_REALTIME_URL = "wss://socket.massive.com/stocks"

    def __init__(self, plan: str):
        self.plan = plan.lower()
        self.features = self.PLAN_FEATURES.get(self.plan, self.PLAN_FEATURES["starter"])

    def has(self, feature: str) -> bool: return feature in self.features
    def can_addon(self, addon: str) -> bool:
        return self.plan in self.ADDONS.get(addon, {}).get("plans", [])
    def addon_info(self, addon: str) -> dict: return self.ADDONS.get(addon, {})

    @property
    def ws_url(self) -> str:
        return self.WS_REALTIME_URL if self.plan == "advanced" else self.WS_DELAYED_URL

    @property
    def available(self) -> list[str]: return sorted(self.features)
    @property
    def missing(self) -> list[str]:
        all_f = {"reference", "ohlcv", "technicals", "corp_actions", "news", "sentiment",
                 "snapshots", "short_interest", "short_volume", "trades", "quotes",
                 "financials", "flat_files", "websocket", "intraday_bars"}
        return sorted(all_f - self.features)

    def __repr__(self) -> str:
        return f"Capabilities(plan={self.plan!r}, available={len(self.features)}, missing_addons={len(self.missing)})"


# ═══════════════════════════════════════════════════════════════════════
# Rate Limiter
# ═══════════════════════════════════════════════════════════════════════

class RateLimiter:
    """Thread-safe token bucket."""
    def __init__(self, max_calls: int = 100, window: float = 60.0, polite_floor: float = 0.001):
        self.max_tokens = max_calls; self.window = window; self.polite_floor = polite_floor
        self.unlimited = (max_calls == 0); self.zero_delay = (max_calls < 0)
        self.tokens = float(max(max_calls, 1))
        self.last_refill = time.monotonic(); self.lock = threading.Lock()
        self.total_acquired = 0; self.total_waited = 0.0

    def acquire(self) -> float:
        if self.zero_delay: self.total_acquired += 1; return 0.0
        if self.unlimited:
            wait = 0.0
            with self.lock:
                now = time.monotonic(); elapsed = now - self.last_refill
                if elapsed < self.polite_floor:
                    wait = self.polite_floor - elapsed; time.sleep(wait)
                self.last_refill = time.monotonic()
                self.total_acquired += 1; self.total_waited += wait
            return wait
        with self.lock:
            now = time.monotonic()
            self.tokens = min(self.max_tokens, self.tokens + (now - self.last_refill) * (self.max_tokens / self.window))
            self.last_refill = now
            if self.tokens >= 1.0: self.tokens -= 1.0; self.total_acquired += 1; return 0.0
            wait = (1.0 - self.tokens) * (self.window / self.max_tokens); self.tokens = 0.0
        time.sleep(wait)
        with self.lock: self.last_refill = time.monotonic(); self.tokens -= 1.0
        self.total_acquired += 1; self.total_waited += wait; return wait

    @property
    def stats(self) -> dict:
        m = "unlimited" if self.unlimited else "zero_delay" if self.zero_delay else f"{self.max_tokens}/{self.window}s"
        return {"acquired": self.total_acquired, "waited_total": round(self.total_waited, 3), "mode": m}


# ═══════════════════════════════════════════════════════════════════════
# PolygonClient
# ═══════════════════════════════════════════════════════════════════════

class PolygonClient:
    """Plan-aware Polygon.io REST + WebSocket client. Default: Starter ($29/mo)."""

    BASE_URL = "https://api.polygon.io"
    USER_AGENT = "Hermes-PolygonClient/5.0"
    CACHEABLE_ENDPOINTS = {
        "/v3/reference/tickers/types", "/v3/reference/exchanges",
        "/v3/reference/conditions", "/v1/marketstatus/upcoming",
    }

    def __init__(self, api_key: Optional[str] = None,
                 rate_limit: Optional[tuple[int, float]] = None,
                 plan: Optional[str] = None,
                 timeout: int = 15, retries: int = 3,
                 cache_refs: bool = True, max_workers: int = DEFAULT_MAX_WORKERS):
        """Initialize PolygonClient with API key, plan, and rate-limit config."""
        self.api_key = api_key or _load_api_key()
        self.timeout = timeout; self.retries = retries; self.max_workers = max_workers
        p = (plan or POLYGON_PLAN).lower()
        calls, window = rate_limit if rate_limit else PLAN_LIMITS.get(p, PLAN_LIMITS["starter"])
        self.plan = p
        self.capabilities = Capabilities(p)
        self.limiter = RateLimiter(calls, window)
        self._cache: dict[str, dict] = {} if cache_refs else None
        self._cache_lock = threading.Lock()

    def can(self, feature: str) -> bool:
        """Check if the current plan supports a specific feature."""
        return self.capabilities.has(feature)
    def __repr__(self) -> str:
        return f"PolygonClient(plan={self.plan!r}, caps={len(self.capabilities.features)} features, calls={self.limiter.total_acquired})"
    def __enter__(self): return self
    def __exit__(self, *args): self._cache = None

    # ═══════════════════════════════════════════════════════════════════
    # Core HTTP
    # ═══════════════════════════════════════════════════════════════════

    def _get(self, path: str, params: Optional[dict] = None, _retry: int = 0) -> dict:
        if params is None: params = {}
        params["apiKey"] = self.api_key
        url = f"{self.BASE_URL}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": self.USER_AGENT})
        self.limiter.acquire()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:500] if e.fp else ""
            if _retry < self.retries and (e.code == 429 or 500 <= e.code < 600):
                time.sleep(min(2 ** (_retry + 1), 30))
                return self._get(path, params, _retry + 1)
            return {"status": "ERROR", "http_status": e.code, "error": body}
        except Exception as e:
            if _retry < self.retries:
                time.sleep(min(2 ** (_retry + 1), 30))
                return self._get(path, params, _retry + 1)
            return {"status": "ERROR", "error": str(e)[:500]}

    def _ok(self, resp: dict) -> bool:
        return isinstance(resp, dict) and resp.get("status") in ("OK", "DELAYED")

    def _success(self, resp: dict, path: str = "") -> bool:
        """Endpoint-aware success check.  Aggs endpoints have no ``status``
        field on success — they return ``{\"resultsCount\": N, \"results\": [...]}``.
        Everything else uses the standard ``status`` field."""
        if not isinstance(resp, dict):
            return False
        if "/v2/aggs" in path:
            return "results" in resp and resp.get("status") != "ERROR"
        return resp.get("status") in ("OK", "DELAYED")

    def _addon_get(self, addon_key: str, path: str, params: dict) -> dict:
        if not self.capabilities.can_addon(addon_key):
            info = self.capabilities.addon_info(addon_key)
            return {"status": "ADDON_REQUIRED", "addon": addon_key,
                    "price": info.get("price", "?"), "desc": info.get("desc", ""),
                    "plans": info.get("plans", [])}
        resp = self._get(path, params)
        if resp.get("http_status") == 403 or resp.get("status") == "NOT_AUTHORIZED":
            info = self.capabilities.addon_info(addon_key)
            return {"status": "ADDON_REQUIRED", "addon": addon_key,
                    "price": info.get("price", "?"), "desc": info.get("desc", ""),
                    "plans": info.get("plans", [])}
        return resp

    # ═══════════════════════════════════════════════════════════════════
    # Caching + Pagination
    # ═══════════════════════════════════════════════════════════════════

    def _cached_get(self, path: str, params: Optional[dict] = None) -> dict:
        if self._cache is None: return self._get(path, params)
        ep = params or {}
        ck = path + "?" + "&".join(f"{k}={v}" for k, v in sorted(ep.items()) if k != "apiKey")
        with self._cache_lock:
            if ck in self._cache: return self._cache[ck]
        resp = self._get(path, params)
        if self._ok(resp) and path in self.CACHEABLE_ENDPOINTS:
            with self._cache_lock: self._cache[ck] = resp
        return resp

    def clear_cache(self):
        """Clear the in-memory reference data cache."""
        if self._cache is not None:
            with self._cache_lock: self._cache.clear()

    def _paginated_get(self, path: str, params: dict, max_pages: int = 50) -> dict:
        """Fetch all pages of a paginated endpoint, accumulating results.

        For aggs endpoints the polygon API advances the start timestamp in the
        URL path — the ``cursor`` query param is cosmetic.  We follow
        ``next_url`` as-is so pagination actually progresses.

        For all other endpoints (tickers, dividends, news, etc.) we extract
        the cursor and reuse the original path — standard cursor pagination.
        """
        is_aggs = "/v2/aggs" in path
        resp = self._get(path, params)
        if not self._success(resp, path):
            return resp
        all_results = list(resp.get("results", []))
        next_url = resp.get("next_url")
        pages = 0
        while next_url and pages < max_pages:
            if is_aggs:
                parsed = urllib.parse.urlparse(next_url)
                page = self._get(parsed.path, dict(urllib.parse.parse_qsl(parsed.query)))
            else:
                cursor = self._extract_cursor(next_url)
                if not cursor:
                    break
                page = self._get(path, {"cursor": cursor})
            all_results.extend(page.get("results", []))
            next_url = page.get("next_url")
            pages += 1
        resp["results"] = all_results
        resp["count"] = len(all_results)
        return resp

    def _page_iterator(self, path: str, params: dict, max_pages: int = 50) -> Iterator[dict]:
        """Yield raw page responses from a paginated endpoint."""
        is_aggs = "/v2/aggs" in path
        resp = self._get(path, params)
        if not self._success(resp, path):
            return
        yield resp
        next_url = resp.get("next_url")
        pages = 0
        while next_url and pages < max_pages:
            if is_aggs:
                parsed = urllib.parse.urlparse(next_url)
                page = self._get(parsed.path, dict(urllib.parse.parse_qsl(parsed.query)))
            else:
                cursor = self._extract_cursor(next_url)
                if not cursor:
                    break
                page = self._get(path, {"cursor": cursor})
            yield page
            next_url = page.get("next_url")
            pages += 1
    @staticmethod
    def _extract_cursor(next_url: str) -> Optional[str]:
        """Extract the cursor param from a Polygon next_url."""
        return urllib.parse.parse_qs(urllib.parse.urlparse(next_url).query).get("cursor", [None])[0]

    def iter_pages(self, path: str, params: dict, max_pages: int = 50) -> Iterator[dict]:
        """Yield raw page responses from a paginated endpoint (public alias)."""
        return self._page_iterator(path, params, max_pages)

    def _bulk_fetch(self, method: Callable, tickers: list[str],
                    per_ticker_timeout: float = 300.0, **kwargs) -> dict[str, dict]:
        """Fetch data for multiple tickers in parallel via ThreadPoolExecutor.

        IMPORTANT: as_completed() timeout prevents a single stuck HTTP call
        from freezing the entire pool. Per warehouse pitfall #12 — without
        this, 8+ workers can hang indefinitely on one bad connection.
        """
        results = {}
        if not tickers:
            return results
        real_max = min(self.max_workers, len(tickers))
        pool = ThreadPoolExecutor(max_workers=real_max)
        futures = {pool.submit(method, t, **kwargs): t for t in tickers}
        processed: set[str] = set()
        try:
            try:
                for f in as_completed(futures, timeout=per_ticker_timeout):
                    t = futures[f]
                    processed.add(t)
                    try:
                        results[t] = f.result(timeout=60)
                    except TimeoutError:
                        results[t] = {"status": "ERROR", "error": "per-ticker timeout (60s)", "ticker": t}
                    except Exception as e:
                        results[t] = {"status": "ERROR", "error": str(e), "ticker": t}
            except TimeoutError:
                for f, t in futures.items():
                    if t not in processed:
                        f.cancel()
                        results[t] = {"status": "ERROR", "error": "bulk as_completed timeout", "ticker": t}
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        return results

    def _ticker_paginated(self, endpoint: str, ticker: str, **params) -> dict:
        params["ticker"] = ticker.upper()
        paginate = params.pop("paginate", False)
        return self._paginated_get(endpoint, params) if paginate else self._get(endpoint, params)

    # ═══════════════════════════════════════════════════════════════════
    # TICKERS
    # ═══════════════════════════════════════════════════════════════════

    def all_tickers(self, ticker: Optional[str] = None, active: bool = True,
                    market: str = "stocks", limit: int = 100, paginate: bool = False) -> dict:
        """Search or list all tickers, with optional filtering by ticker symbol."""
        params = {"active": str(active).lower(), "market": market, "limit": limit}
        if ticker: params["ticker"] = ticker.upper()
        return self._paginated_get("/v3/reference/tickers", params) if paginate else self._get("/v3/reference/tickers", params)

    def ticker_overview(self, ticker: str) -> dict:
        """Get company overview details for a single ticker (name, market cap, sector, etc.)."""
        return self._get(f"/v3/reference/tickers/{ticker.upper()}")

    def ticker_types(self) -> dict:
        """List all available ticker types (CS, ADR, ETF, etc.). Cached."""
        return self._cached_get("/v3/reference/tickers/types")
    def related_companies(self, ticker: str) -> dict:
        """Get tickers of companies related to the given ticker."""
        return self._get(f"/v1/related-companies/{ticker.upper()}")

    def ticker_overview_bulk(self, tickers: list[str]) -> dict[str, dict]:
        """Fetch overviews for multiple tickers in parallel via ThreadPoolExecutor."""
        return self._bulk_fetch(self.ticker_overview, tickers)

    # ═══════════════════════════════════════════════════════════════════
    # AGGREGATE BARS
    # ═══════════════════════════════════════════════════════════════════

    def custom_bars(self, ticker: str, from_date: str, to_date: str,
                    timespan: str = "day", multiplier: int = 1,
                    limit: int = 50000, sort: str = "asc", paginate: bool = False) -> dict:
        """Fetch OHLCV aggregate bars for a ticker over a date range."""
        path = f"/v2/aggs/ticker/{ticker.upper()}/range/{multiplier}/{timespan}/{from_date}/{to_date}"
        params = {"limit": limit, "sort": sort}
        return self._paginated_get(path, params) if paginate else self._get(path, params)

    def grouped_daily(self, target_date: str, paginate: bool = True) -> dict:
        """Fetch daily OHLCV for all US stocks on a given date."""
        path = f"/v2/aggs/grouped/locale/us/market/stocks/{target_date}"
        return self._paginated_get(path, {"limit": 50000}) if paginate else self._get(path, {"limit": 50000})

    def open_close(self, ticker: str, target_date: str) -> dict:
        """Get the open, close, and after-hours prices for a ticker on a date."""
        return self._get(f"/v1/open-close/{ticker.upper()}/{target_date}")

    def previous_day(self, ticker: str) -> dict:
        """Get the previous trading day's OHLCV bar for a ticker."""
        return self._get(f"/v2/aggs/ticker/{ticker.upper()}/prev")

    def custom_bars_bulk(self, tickers: list[str], from_date: str, to_date: str,
                         timespan: str = "day", multiplier: int = 1) -> dict[str, dict]:
        """Fetch custom bars for multiple tickers in parallel."""
        return self._bulk_fetch(self.custom_bars, tickers, from_date=from_date,
                                to_date=to_date, timespan=timespan, multiplier=multiplier)

    # ═══════════════════════════════════════════════════════════════════
    # INTRADAY BARS + VOLUME PROFILE (Starter+)
    # ═══════════════════════════════════════════════════════════════════

    def intraday_bars(self, ticker: str, days: int = 5,
                      timespan: str = "minute") -> dict:
        """Pull intraday bars (minute or hour) for the last N trading days.

        Pulls days*2 calendar days of data to ensure N full trading days
        are captured. Use volume_profile() for time-aligned accumulated
        volume analysis.
        """
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=max(days * 2, 14))
        if timespan not in ("minute", "hour"):
            timespan = "minute"
        return self.custom_bars(ticker, start.isoformat(), end.isoformat(),
                                timespan=timespan, limit=50000, paginate=True)

    def volume_profile(self, ticker: str, days: int = 20,
                       bin_minutes: int = 5, timespan: str = "minute") -> dict:
        """Build time-aligned accumulated volume profile.

        Pulls N+10 days of intraday bars (padding ensures enough complete
        trading days), then computes for each time bin:
        - avg cumulative volume across all days
        - std dev, CV%, P25, P75
        - above/below thresholds (1.5x and 0.5x avg)

        Returns:
            {"ticker": "AAPL", "days_sampled": 26, "total_bars": 21188,
             "snapshots": [{"time": "09:30", "avg_cum_vol": ..., "cv_pct": ...}, ...],
             "profile": {minute_bin_et: {avg, std, cv_pct, p25, p75, count}, ...}}
        """
        resp = self.intraday_bars(ticker, days=days + 10, timespan=timespan)
        if not self._ok(resp):
            return {"status": "ERROR", "error": resp.get("error", "Failed to pull intraday bars")}

        results = resp.get("results", [])
        day_data = defaultdict(lambda: {"bars": [], "cum_vol": []})
        for bar in results:
            ts_sec = bar["t"] / 1000
            dt = datetime.fromtimestamp(int(ts_sec), tz=timezone.utc)
            trade_date = dt.strftime("%Y-%m-%d")
            minute_of_day = dt.hour * 60 + dt.minute
            vol = float(bar.get("v", 0))
            day_data[trade_date]["bars"].append((minute_of_day, vol))

        for day in list(day_data.keys()):
            bars_sorted = sorted(day_data[day]["bars"], key=lambda x: x[0])
            cum = 0.0
            for minute, vol in bars_sorted:
                cum += vol
                day_data[day]["cum_vol"].append((minute, cum))

        aligned = defaultdict(list)
        for day in list(day_data.keys()):
            for minute, cum_vol in day_data[day]["cum_vol"]:
                bin_key = (minute // bin_minutes) * bin_minutes
                aligned[bin_key].append(cum_vol)

        profile = {}
        snapshots = []
        SNAPSHOT_TIMES_ET = [570, 630, 720, 780, 840, 900, 930]

        for minute_bin in sorted(aligned):
            vols = aligned[minute_bin]
            if len(vols) < 3:
                continue
            avg = sum(vols) / len(vols)
            variance = sum((v - avg) ** 2 for v in vols) / len(vols)
            std = variance ** 0.5
            cv_pct = (std / avg * 100) if avg > 0 else 0
            sorted_vols = sorted(vols)
            p25 = sorted_vols[len(vols) // 4]
            p75 = sorted_vols[3 * len(vols) // 4]

            et_minute = minute_bin - 4 * 60
            if et_minute < 0:
                continue

            entry = {"avg": round(avg), "std": round(std), "cv_pct": round(cv_pct, 1),
                     "p25": round(p25), "p75": round(p75), "count": len(vols),
                     "above_15x": round(avg * 1.5), "below_05x": round(avg * 0.5)}
            profile[et_minute] = entry

            if et_minute in SNAPSHOT_TIMES_ET:
                h, m = divmod(et_minute, 60)
                snapshots.append({
                    "time": f"{h:02d}:{m:02d}",
                    "avg_cum_vol": round(avg),
                    "std": round(std),
                    "cv_pct": round(cv_pct, 1),
                    "above_15x": round(avg * 1.5),
                    "below_05x": round(avg * 0.5),
                })

        return {
            "status": "OK",
            "ticker": ticker.upper(),
            "days_sampled": len(day_data),
            "total_bars": len(results),
            "bin_minutes": bin_minutes,
            "timezone": "ET",
            "snapshots": snapshots,
            "profile": profile,
        }

    def volume_screener_snapshot(self, ticker: str, days: int = 20,
                                  timespan: str = "minute") -> dict:
        """Quick volume screener: compare today's accumulated volume vs 20-day average.

        Returns time-aligned comparisons at key intraday milestones:
        09:30, 10:30, 12:00, 13:00, 14:00, 15:00, 15:30 ET.
        Signal: above (>1.5x avg), below (<0.5x avg), normal.
        """
        profile = self.volume_profile(ticker, days=days, timespan=timespan)
        if not self._ok(profile):
            return profile

        today = date.today().isoformat()
        today_bars = self.custom_bars(ticker, today, today, timespan=timespan, limit=50000)
        today_cum = 0.0
        today_profile = {}
        if self._ok(today_bars):
            for bar in today_bars.get("results", []):
                ts_sec = bar["t"] / 1000
                dt = datetime.fromtimestamp(int(ts_sec), tz=timezone.utc)
                minute_of_day = dt.hour * 60 + dt.minute
                today_cum += float(bar.get("v", 0))
                today_profile[minute_of_day - 4 * 60] = today_cum

        comparisons = []
        for snap in profile.get("snapshots", []):
            time_label = snap["time"]
            h, m = map(int, time_label.split(":"))
            et_min = h * 60 + m
            avg = snap["avg_cum_vol"]
            today_vol = today_profile.get(et_min, 0)
            ratio = today_vol / avg if avg > 0 else 0
            signal = "above" if ratio > 1.5 else "below" if ratio < 0.5 else "normal"
            comparisons.append({
                "time": time_label, "today_cum_vol": round(today_vol),
                "avg_20d_cum_vol": avg, "ratio": round(ratio, 2), "signal": signal,
            })

        return {"status": "OK", "ticker": ticker.upper(), "date": today,
                "days_sampled": profile.get("days_sampled", 0), "comparisons": comparisons}

    # ═══════════════════════════════════════════════════════════════════
    # SNAPSHOTS
    # ═══════════════════════════════════════════════════════════════════

    def snapshot(self, ticker: str) -> dict:
        """Get a real-time snapshot of a single ticker (OHLCV, minute, previous day)."""
        return self._get(f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker.upper()}")
    def snapshot_all(self) -> dict:
        """Get real-time snapshots for all US stocks."""
        return self._get("/v2/snapshot/locale/us/markets/stocks/tickers")
    def top_gainers(self) -> dict:
        """Get the top gaining stocks for the current trading day."""
        return self._get("/v2/snapshot/locale/us/markets/stocks/gainers")
    def top_losers(self) -> dict:
        """Get the top losing stocks for the current trading day."""
        return self._get("/v2/snapshot/locale/us/markets/stocks/losers")
    def snapshot_bulk(self, tickers: list[str]) -> dict[str, dict]:
        """Fetch snapshots for multiple tickers in parallel."""
        return self._bulk_fetch(self.snapshot, tickers)

    # ═══════════════════════════════════════════════════════════════════
    # TECHNICALS
    # ═══════════════════════════════════════════════════════════════════

    def _indicator(self, name: str, ticker: str, **params) -> dict:
        return self._get(f"/v1/indicators/{name}/{ticker.upper()}", params)

    def sma(self, ticker: str, window: int = 50, timespan: str = "day",
            limit: int = 100, order: str = "desc") -> dict:
        """Simple Moving Average indicator."""
        return self._indicator("sma", ticker, timespan=timespan, window=window, limit=limit, order=order)
    def ema(self, ticker: str, window: int = 20, timespan: str = "day",
            limit: int = 100, order: str = "desc") -> dict:
        """Exponential Moving Average indicator."""
        return self._indicator("ema", ticker, timespan=timespan, window=window, limit=limit, order=order)
    def macd(self, ticker: str, timespan: str = "day", limit: int = 100, order: str = "desc") -> dict:
        """MACD (Moving Average Convergence Divergence) indicator."""
        return self._indicator("macd", ticker, timespan=timespan, limit=limit, order=order)
    def rsi(self, ticker: str, window: int = 14, timespan: str = "day",
            limit: int = 100, order: str = "desc") -> dict:
        """Relative Strength Index indicator."""
        return self._indicator("rsi", ticker, timespan=timespan, window=window, limit=limit, order=order)

    # ═══════════════════════════════════════════════════════════════════
    # MARKET OPERATIONS
    # ═══════════════════════════════════════════════════════════════════

    def exchanges(self) -> dict:
        """List all stock exchanges. Cached."""
        return self._cached_get("/v3/reference/exchanges")
    def market_holidays(self) -> dict:
        """Get upcoming market holidays. Cached."""
        return self._cached_get("/v1/marketstatus/upcoming")
    def market_status(self) -> dict:
        """Get current market status (open/closed for each exchange)."""
        return self._get("/v1/marketstatus/now")
    def condition_codes(self, limit: int = 100) -> dict:
        """List trade condition codes. Cached."""
        return self._cached_get("/v3/reference/conditions", {"limit": limit})

    # ═══════════════════════════════════════════════════════════════════
    # CORPORATE
    # ═══════════════════════════════════════════════════════════════════

    def dividends(self, ticker: str, limit: int = 100, paginate: bool = False) -> dict:
        """Get dividend history for a ticker."""
        return self._ticker_paginated("/v3/reference/dividends", ticker, limit=limit, paginate=paginate)
    def stock_splits(self, ticker: str, limit: int = 100, paginate: bool = False) -> dict:
        """Get stock split history for a ticker."""
        return self._ticker_paginated("/v3/reference/splits", ticker, limit=limit, paginate=paginate)

    # ═══════════════════════════════════════════════════════════════════
    # NEWS & SENTIMENT
    # ═══════════════════════════════════════════════════════════════════

    def ticker_news(self, ticker: str, limit: int = 10,
                    order: str = "desc", sort: str = "published_utc",
                    published_utc: Optional[str] = None, paginate: bool = False) -> dict:
        """Get news articles mentioning a ticker, with optional date filtering."""
        params = {"ticker": ticker.upper(), "limit": limit, "order": order, "sort": sort}
        if published_utc: params["published_utc"] = published_utc
        return self._paginated_get("/v2/reference/news", params) if paginate else self._get("/v2/reference/news", params)

    def news_sentiment(self, ticker: str, limit: int = 20) -> dict:
        """Extract sentiment signals from news articles for a ticker."""
        resp = self.ticker_news(ticker, limit=limit, order="desc")
        if not self._ok(resp): return resp
        articles = resp.get("results", [])
        signals = []
        counts = {"positive": 0, "negative": 0, "neutral": 0}
        for a in articles:
            for ins in a.get("insights", []):
                if ins.get("ticker", "").upper() == ticker.upper():
                    sent = ins.get("sentiment", "neutral")
                    counts[sent] = counts.get(sent, 0) + 1
                    signals.append({"date": a.get("published_utc", ""), "title": a.get("title", ""),
                                    "sentiment": sent, "reasoning": ins.get("sentiment_reasoning", ""),
                                    "publisher": a.get("publisher", {}).get("name", "")})
        return {"status": "OK", "ticker": ticker.upper(), "articles": len(articles),
                "sentiment_counts": counts, "signals": signals}

    # ═══════════════════════════════════════════════════════════════════
    # FINANCIALS
    # ═══════════════════════════════════════════════════════════════════

    def financials(self, ticker: str, limit: Optional[int] = None,
                   filing_date: Optional[str] = None,
                   period_of_report_date: Optional[str] = None,
                   paginate: bool = True, sort_desc: bool = True) -> dict:
        """Get financial statements (balance sheet, P&L, ratios) for a ticker.

        IMPORTANT: Polygon returns oldest-first by default. sort_desc=True
        sorts by calendarDate descending (newest quarters first). On Starter
        without the $29/mo Financials add-on, data ends at 2020-03-31.
        """
        endpoint = f"/v2/reference/financials/{ticker.upper()}"
        params = {}
        if filing_date: params["filing_date"] = filing_date
        if period_of_report_date: params["period_of_report_date"] = period_of_report_date
        if not paginate: return self._get(endpoint, params)
        resp = self._paginated_get(endpoint, params)
        if not self._ok(resp): return resp
        results = resp.get("results", [])
        if results and sort_desc:
            results.sort(key=lambda r: r.get("calendarDate", ""), reverse=True)
        if limit and limit > 0: results = results[:limit]
        resp["results"] = results; resp["count"] = len(results)
        return resp

    # ═══════════════════════════════════════════════════════════════════
    # SHORT DATA
    # ═══════════════════════════════════════════════════════════════════

    def short_interest(self, ticker: str, limit: int = 100, paginate: bool = False) -> dict:
        """Get short interest data for a ticker."""
        return self._ticker_paginated("/stocks/v1/short-interest", ticker,
                                       limit=limit, sort="settlement_date.desc", paginate=paginate)
    def short_volume(self, ticker: str, limit: int = 100, paginate: bool = False) -> dict:
        """Get daily short volume data for a ticker."""
        return self._ticker_paginated("/stocks/v1/short-volume", ticker, limit=limit, paginate=paginate)

    # ═══════════════════════════════════════════════════════════════════
    # FLAT FILES
    # ═══════════════════════════════════════════════════════════════════

    @staticmethod
    def flat_files_info() -> dict:
        """Return metadata about available Polygon flat files (S3 paths, contents, format)."""
        return {
            "access": "S3-compatible client or Polygon.io File Browser",
            "endpoint": "https://files.polygon.io",
            "datasets": {
                "day_aggs": {"s3_path": "us_stocks_sip/day_aggs_v1/{YYYY}/{MM}/{DD}.csv",
                             "contents": "Daily OHLCV for all US stocks"},
                "minute_aggs": {"s3_path": "us_stocks_sip/minute_aggs_v1/{YYYY}/{MM}/{DD}.csv",
                                "contents": "Per-minute OHLCV for all US stocks"},
                "trades": {"s3_path": "us_stocks_sip/trades_v1/{YYYY}/{MM}/{DD}.csv",
                           "contents": "Tick-level trades, nanosecond timestamps"},
                "quotes": {"s3_path": "us_stocks_sip/quotes_v1/{YYYY}/{MM}/{DD}.csv",
                           "contents": "NBBO bid/ask, nanosecond timestamps"},
            },
            "format": "CSV with headers, gzip compressed",
            "adjusted": False,
            "plan_availability": "All plans including Free",
            "note": "Data is UNadjusted. For split/dividend-adjusted data, use REST API with adjusted=true.",
        }

    # ═══════════════════════════════════════════════════════════════════
    # BENZINGA
    # ═══════════════════════════════════════════════════════════════════

    def benzinga_ratings(self, ticker: str, limit: int = 10, sort: str = "date.desc") -> dict:
        """Analyst ratings/upgrades/downgrades (Benzinga add-on, $99/mo)."""
        return self._addon_get("benzinga_ratings", "/benzinga/v1/ratings",
                               {"ticker": ticker.upper(), "limit": limit, "sort": sort})
    def benzinga_earnings(self, ticker: str, limit: int = 10, sort: str = "date.desc") -> dict:
        """Earnings surprises — actuals vs estimates (Benzinga add-on, $99/mo)."""
        return self._addon_get("benzinga_earnings", "/benzinga/v1/earnings",
                               {"ticker": ticker.upper(), "limit": limit, "sort": sort})
    def benzinga_bulls_bears(self, ticker: str, limit: int = 3) -> dict:
        """Bull and bear case summaries (Benzinga add-on, $99/mo)."""
        return self._addon_get("benzinga_bulls_bears", "/benzinga/v1/bulls-bears-say",
                               {"ticker": ticker.upper(), "limit": limit})
    def benzinga_news(self, ticker: str, limit: int = 10, sort: str = "published.desc") -> dict:
        """Premium Benzinga news articles (Benzinga add-on, $99/mo)."""
        return self._addon_get("benzinga_news", "/benzinga/v1/news",
                               {"ticker": ticker.upper(), "limit": limit, "sort": sort})

    # ═══════════════════════════════════════════════════════════════════
    # WEBSOCKET STREAMING
    # ═══════════════════════════════════════════════════════════════════

    def websocket_stream(self, tickers: list[str],
                         channels: list[str] = None) -> Iterator[dict]:
        """Connect to WebSocket, authenticate, subscribe, and yield bars.

        Uses 15-min delayed feed on Starter. Real-time on Advanced.
        Channels default to A (per-second) + AM (per-minute).
        Uses deque for O(1) pop from the message queue.

        Usage:
            for bar in client.websocket_stream(["AAPL", "MSFT"]):
                print(f"{bar['sym']}: V={bar.get('v')} AV={bar.get('av')}")
                if enough_data: break
        """
        if channels is None:
            channels = ["A", "AM"]

        import websocket as ws_lib
        queue = deque()
        lock = threading.Lock()
        auth_ok = threading.Event()

        def on_message(ws, message):
            data = json.loads(message)
            if isinstance(data, list):
                for item in data:
                    ev = item.get("ev", "")
                    status = item.get("status", "")
                    if status in ("auth_success", "connected"):
                        auth_ok.set()
                    if ev in channels:
                        with lock:
                            queue.append(item)

        def on_open(ws):
            ws.send(json.dumps({"action": "auth", "params": self.api_key}))

        ws = ws_lib.WebSocketApp(self.capabilities.ws_url,
                                  on_open=on_open, on_message=on_message)
        t = threading.Thread(target=ws.run_forever, daemon=True)
        t.start()

        if not auth_ok.wait(timeout=15):
            ws.close()
            raise RuntimeError("WebSocket authentication timed out")

        ticker_list = ",".join(t.upper() for t in tickers)
        chan_params = ",".join(f"{ch}.{ticker_list}" for ch in channels)
        ws.send(json.dumps({"action": "subscribe", "params": chan_params}))

        try:
            while True:
                with lock:
                    while queue:
                        yield queue.popleft()
                time.sleep(0.05)
        finally:
            ws.close()

    # ═══════════════════════════════════════════════════════════════════
    # CONVENIENCE
    # ═══════════════════════════════════════════════════════════════════

    def full_profile(self, ticker: str) -> dict:
        """Fetch a comprehensive profile bundle (overview, bars, snapshot, RSI, news, financials)."""
        t = ticker.upper()
        return {
            "overview": self.ticker_overview(t),
            "prev_day": self.previous_day(t),
            "snapshot": self.snapshot(t),
            "rsi": self.rsi(t, limit=5),
            "dividends": self.dividends(t, limit=5),
            "splits": self.stock_splits(t, limit=5),
            "news_sentiment": self.news_sentiment(t, limit=10),
            "short_interest": self.short_interest(t, limit=3),
            "financials": self.financials(t, limit=4, paginate=False),
        }

    def full_profile_bulk(self, tickers: list[str]) -> dict[str, dict]:
        """Fetch full profiles for multiple tickers in parallel."""
        return self._bulk_fetch(self.full_profile, tickers)


# ═══════════════════════════════════════════════════════════════════════
# Smoke test
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    client = PolygonClient()
    caps = client.capabilities
    print(f"PolygonClient v5 — plan: {client.plan}")
    print(f"  Features: {caps.available}")
    print(f"  Missing:  {caps.missing}")
    print(f"  WS URL:   {caps.ws_url}")
    print()

    # Volume profile
    print("── Volume Profile (AAPL, 20-day) ──")
    vp = client.volume_profile("AAPL", days=20)
    if client._ok(vp):
        print(f"  Days: {vp['days_sampled']} | Bars: {vp['total_bars']:,} | Bins: {len(vp['profile'])}")
        for s in vp.get("snapshots", []):
            print(f"  {s['time']}: avg={s['avg_cum_vol']:>12,}  cv={s['cv_pct']:>5.1f}%")
    else:
        print(f"  ERROR: {vp.get('error', '')}")
