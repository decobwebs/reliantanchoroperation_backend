"""
Simple in-memory rate limiter middleware.
No external dependencies — uses a sliding-window counter per (IP, route).

Default limits:
  - POST /auth/login    → 10 requests / 60 s per IP
  - POST /auth/register → 5  requests / 60 s per IP

On limit breach returns HTTP 429 with Retry-After header.
"""
import time
import asyncio
from collections import defaultdict, deque
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# (ip, path) → deque of request timestamps
_windows: dict = defaultdict(deque)
_lock = asyncio.Lock()

# route_suffix → (max_requests, window_seconds)
RATE_LIMITS: dict = {
    "/auth/login":    (10, 60),
    "/auth/register": (5,  60),
}


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "POST":
            path = request.url.path
            for suffix, (max_req, window) in RATE_LIMITS.items():
                if path.endswith(suffix):
                    ip = _client_ip(request)
                    key = (ip, suffix)
                    now = time.monotonic()

                    async with _lock:
                        dq = _windows[key]
                        # Evict timestamps outside the window
                        while dq and now - dq[0] > window:
                            dq.popleft()

                        if len(dq) >= max_req:
                            retry_after = int(window - (now - dq[0])) + 1
                            return JSONResponse(
                                status_code=429,
                                headers={"Retry-After": str(retry_after)},
                                content={
                                    "success": False,
                                    "data": None,
                                    "message": "Too many requests. Please try again later.",
                                    "errors": [f"Rate limit exceeded. Retry after {retry_after}s."],
                                },
                            )
                        dq.append(now)
                    break

        return await call_next(request)
