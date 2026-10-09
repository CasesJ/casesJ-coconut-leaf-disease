# Inference abuse controls

The supported server entry point is `uvicorn app.main:app` (or `python run.py`).
It protects the expensive inference, export, authentication-token, and streaming
endpoints with the following defaults:

| Surface | Default control |
| --- | --- |
| `POST /detect/image` | Authentication required; 100 inference reservations per user for 24 hours from their first counted upload (Philippine time), 10 per user per minute, and a 10 MB image limit |
| `GET /api/export-csv` | Authentication required; 5 exports per user per minute |
| `POST /auth/verify-token` | 10 checks per source IP per minute |
| `WS /detect/stream` | Authentication required; 2 connections per user, 10 frames per user per second, and 3 MB decoded-frame limit |

Rate-limit responses use HTTP `429` and include `Retry-After`. Uploads that are
not valid images are rejected before they reserve the daily inference quota.
The daily image quota is deliberately reserved before the model runs, including
requests that produce no disease detections, so repeated inference cannot bypass
the resource limit.

## Configuration

All values are environment variables shown in `.env.example`. Keep
`IMAGE_INFERENCE_DAILY_LIMIT=100` to enforce the requested allowance. Limits
must be positive integers. Each user's quota window begins with their first
counted upload and expires exactly 24 hours later. Window timestamps and reset
times use Philippine time (`Asia/Manila`); there is no shared midnight reset.

## Deployment notes

The built-in limiter is concurrency-safe inside one Python process. For multiple
Uvicorn workers or multiple application instances, configure equivalent shared
limits in an API gateway/reverse proxy or replace the in-process counter with a
shared store such as Redis; otherwise each process has its own counters.

By default, source-IP limits use the direct peer address. Set
`TRUST_PROXY_HEADERS=true` only when a trusted proxy strips inbound
`X-Forwarded-For` and sets it itself. This prevents clients from choosing their
own IP rate-limit key.
