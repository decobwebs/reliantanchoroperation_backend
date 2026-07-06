"""
AuditLogMiddleware — writes an AuditLog row for every authenticated write
(POST / PUT / PATCH / DELETE) on /api/v1/, non-blocking.

Does NOT log:
  - Unauthenticated requests (no valid Bearer token sub claim)
  - Rate-limited / rejected requests whose path doesn't start with /api/v1/
  - The token-refresh route (too noisy)
"""
import logging
import time
from typing import Optional
from uuid import UUID

from jose import jwt as jose_jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.database import AsyncSessionLocal
from app.models.audit import AuditLog

logger = logging.getLogger("raoms.audit")

_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_SKIP_EXACT = {"/api/v1/auth/refresh", "/api/v1/health"}


def _user_id_from_request(request: Request) -> Optional[UUID]:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    try:
        claims = jose_jwt.get_unverified_claims(auth[7:])
        sub = claims.get("sub")
        return UUID(sub) if sub else None
    except Exception:
        return None


def _client_ip(request: Request) -> Optional[str]:
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


class AuditLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        t0 = time.perf_counter()
        response = await call_next(request)

        method = request.method
        path = request.url.path

        if method not in _WRITE_METHODS:
            return response
        if not path.startswith("/api/v1/"):
            return response
        if path in _SKIP_EXACT:
            return response

        user_id = _user_id_from_request(request)
        if not user_id:
            return response

        duration_ms = int((time.perf_counter() - t0) * 1000)
        qs = str(request.query_params) or None

        try:
            async with AsyncSessionLocal() as db:
                entry = AuditLog(
                    user_id=user_id,
                    action=f"{method} {path}",
                    entity_type="api",
                    changes={
                        "status_code": response.status_code,
                        "duration_ms": duration_ms,
                        **({"query": qs} if qs else {}),
                    },
                    ip_address=_client_ip(request),
                    user_agent=request.headers.get("User-Agent"),
                )
                db.add(entry)
                await db.commit()
        except Exception as exc:
            logger.warning("audit-log middleware write failed: %s", exc)

        return response
