from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
import logging
import sys

from app.config import settings
from app.routers import health, auth, operations, admin, tasks, trucks, vessels, bdns, notifications, pfis, documents, analytics, portal, invoices, vouchers
from app.middleware.request_id import RequestIDMiddleware
from app.middleware.rate_limit import RateLimitMiddleware

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

app = FastAPI(
    title="RAOMS — Reliant Anchor Operations Management System",
    description="FastAPI backend for managing maritime bunker (fuel) operations.",
    version="1.0.0",
    docs_url="/docs" if settings.is_development else None,
    redoc_url="/redoc" if settings.is_development else None,
)

# ── Middleware (outermost → innermost) ─────────────────────────────────────────
# Order matters: CORS first, then rate limiting, then request ID.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestIDMiddleware)

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
