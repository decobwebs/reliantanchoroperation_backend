from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import settings

_MAX_BYTES = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
_UPLOAD_METHODS = {"POST", "PUT", "PATCH"}


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method in _UPLOAD_METHODS:
            content_length = request.headers.get("content-length")
            if content_length is not None and int(content_length) > _MAX_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={
                        "success": False,
                        "data": None,
                        "message": f"Request body exceeds the {settings.MAX_UPLOAD_SIZE_MB}MB limit",
                        "errors": [f"Content-Length {content_length} exceeds maximum of {_MAX_BYTES} bytes"],
                    },
                )
        return await call_next(request)
