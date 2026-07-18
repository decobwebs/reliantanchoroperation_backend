import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
import logging
import socket
import sys

# ── Sentry (optional — only when SENTRY_DSN is set) ───────────────────────────
_sentry_dsn = os.environ.get("SENTRY_DSN", "")
if _sentry_dsn:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
    sentry_sdk.init(
        dsn=_sentry_dsn,
        integrations=[FastApiIntegration(), SqlalchemyIntegration()],
        traces_sample_rate=0.2,
        send_default_pii=False,
    )

from app.config import settings
from app.routers import health, auth, operations, admin, tasks, trucks, vessels, bdns, notifications, pfis, documents, analytics, portal, invoices, vouchers, vessel_activities
from app.middleware.request_id import RequestIDMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.audit_log import AuditLogMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.middleware.max_body_size import MaxBodySizeMiddleware
from app.services.document_service import ensure_storage_bucket

# ── Logging setup ──────────────────────────────────────────────────────────────
logging.basicConfig(
    stream=sys.stdout,
    level=logging.DEBUG if settings.is_development else logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("raoms")


# ── Startup config validation ──────────────────────────────────────────────────
def _validate_config() -> None:
    warnings = []
    if not settings.SUPABASE_URL:
        warnings.append("SUPABASE_URL is not set")
    if not settings.SUPABASE_ANON_KEY:
        warnings.append("SUPABASE_ANON_KEY is not set")
    if not settings.SUPABASE_SERVICE_KEY:
        warnings.append("SUPABASE_SERVICE_KEY is not set")
    if not settings.DATABASE_URL:
        warnings.append("DATABASE_URL is not set")
    if not settings.RESEND_API_KEY:
        warnings.append("RESEND_API_KEY not set — transactional emails are disabled")
    for w in warnings:
        logger.warning("CONFIG: %s", w)
    if warnings:
        logger.warning("CONFIG: %d configuration warning(s) above", len(warnings))


_validate_config()

# ── Application factory ────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure BOTH storage buckets exist up front, not lazily on first upload —
    # otherwise a fresh project has no 'truck-photos' bucket until someone uploads.
    await ensure_storage_bucket()                    # operation-documents (default)
    await ensure_storage_bucket("truck-photos")      # truck photos
    yield


app = FastAPI(
    title="RAOMS — Reliant Anchor Operations Management System",
    description="FastAPI backend for managing maritime bunker (fuel) operations.",
    version="1.0.0",
    docs_url="/docs" if settings.is_development else None,
    redoc_url="/redoc" if settings.is_development else None,
    lifespan=lifespan,
)

# ── Middleware (outermost → innermost) ─────────────────────────────────────────
# Execution order: SecurityHeaders → CORS → RequestID → RateLimit → AuditLog → route handler
# AuditLogMiddleware is innermost so it only logs requests that pass rate limiting.
app.add_middleware(AuditLogMiddleware)   # innermost — added first
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    # Only honor the localhost regex in development — in production the explicit
    # origin allow-list is authoritative (avoids trusting any localhost origin).
    allow_origin_regex=settings.CORS_ORIGIN_REGEX if settings.is_development else None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(MaxBodySizeMiddleware)
app.add_middleware(SecurityHeadersMiddleware)  # outermost — added last

# ── Exception handlers ─────────────────────────────────────────────────────────

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "data": None,
            "message": exc.detail,
            "errors": [exc.detail] if isinstance(exc.detail, str) else [str(exc.detail)],
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = []
    for error in exc.errors():
        field = " -> ".join(str(loc) for loc in error.get("loc", []))
        msg = error.get("msg", "Validation error")
        errors.append(f"{field}: {msg}" if field else msg)

    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "data": None,
            "message": "Validation failed",
            "errors": errors,
        },
    )


@app.exception_handler(socket.gaierror)
async def dns_exception_handler(request: Request, exc: socket.gaierror):
    logger.error("DNS lookup failed: %s", str(exc))
    return JSONResponse(
        status_code=503,
        content={
            "success": False,
            "data": None,
            "message": "Network/DNS lookup failed",
            "errors": [
                "A configured external host could not be resolved. Check DATABASE_URL, SUPABASE_URL, DNS/internet connectivity, and firewall/VPN settings."
            ],
        },
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception: %s", str(exc))
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "data": None,
            "message": "Internal server error",
            "errors": [str(exc)] if settings.is_development else ["An unexpected error occurred"],
        },
    )


# ── Routes ─────────────────────────────────────────────────────────────────────

API_PREFIX = "/api/v1"

app.include_router(health.router, prefix=API_PREFIX)
app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(operations.router, prefix=API_PREFIX)
app.include_router(admin.router, prefix=API_PREFIX)
app.include_router(tasks.router, prefix=API_PREFIX)
app.include_router(trucks.router, prefix=API_PREFIX)
app.include_router(vessels.router, prefix=API_PREFIX)
app.include_router(bdns.router, prefix=API_PREFIX)
app.include_router(notifications.router, prefix=API_PREFIX)
app.include_router(pfis.router, prefix=API_PREFIX)
app.include_router(documents.router, prefix=API_PREFIX)
app.include_router(analytics.router, prefix=API_PREFIX)
app.include_router(portal.router, prefix=API_PREFIX)
app.include_router(invoices.router, prefix=API_PREFIX)
app.include_router(vouchers.router, prefix=API_PREFIX)
app.include_router(vessel_activities.router, prefix=API_PREFIX)


@app.get("/", include_in_schema=False)
async def root():
    return {
        "success": True,
        "data": {
            "name": "RAOMS API",
            "version": "1.0.0",
            "docs": "/docs",
        },
        "message": "Reliant Anchor Operations Management System",
        "errors": [],
    }
