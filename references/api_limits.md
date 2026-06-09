# Polygon.io API Rate Limits

**Source:** https://polygon.io/knowledge-base/article/what-is-the-request-limit-for-polygons-restful-apis

## Official Limits

| Plan | Price | API Calls | Practical Ceiling | Historical Data |
|------|-------|-----------|-------------------|-----------------|
| **Basic (Free)** | $0 | **5/min** | 5/min (hard limit) | 2 years |
| **Starter** | $29/mo | **Unlimited** | 100/sec (6,000/min) | 5 years |
| **Developer** | $79/mo | **Unlimited** | 100/sec (6,000/min) | 10 years |
| **Advanced** | $199/mo | **Unlimited** | 100/sec (6,000/min) | 20+ years |

> *"While we do not have a specific rate limit, we do monitor usage to ensure that no single user affects the quality of service for others. To avoid any throttling issues, we recommend staying under 100 requests per second."*
> — Polygon.io Knowledge Base

## PolygonClient Configuration

```python
from polygon_client import PolygonClient

# Auto-detect from POLYGON_PLAN env var (default: "starter")
client = PolygonClient()

# Explicit plan
client = PolygonClient(plan="starter")     # 6,000/min
client = PolygonClient(plan="free")        # 5/min
client = PolygonClient(plan="unlimited")   # no cap, 1ms floor

# Manual override
client = PolygonClient(rate_limit=(300, 60))   # 300 calls/min
client = PolygonClient(rate_limit=(0, 1))      # unlimited, 1ms floor
client = PolygonClient(rate_limit=(-1, 1))     # zero delay (no floor)

# Check current mode
print(client.plan)                            # "starter"
print(client.limiter.stats)                   # {"acquired": 42, "waited_total": 0.0, "mode": "6000/60.0s"}
```

## Environment Variable

Set `POLYGON_PLAN` in `~/.hermes/.env` to auto-configure the client:

```bash
POLYGON_PLAN=starter
```

## RateLimiter Modes

| Mode | `rate_limit` | Behavior |
|------|-------------|----------|
| **Token bucket** | `(N, W)` where N > 0 | Refill N tokens every W seconds. Blocks when empty. |
| **Unlimited** | `(0, W)` | No rate cap. 1ms floor between calls to be polite. |
| **Zero delay** | `(-N, W)` | No rate cap. No floor. Use with caution. |
| **Free tier** | Auto: `(5, 60)` | 5 calls/min. Blocks for 12s between calls. |
| **Paid tier** | Auto: `(6000, 60)` | 100 calls/sec. Blocks only above that. |
