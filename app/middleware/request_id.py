"""
Request ID middleware — injects a unique X-Request-ID into every response.
If the client sends an X-Request-ID header, it is echoed back; otherwise a
new UUID is generated.  The ID is also stored on request.state so downstream
handlers (e.g. logging) can reference it.
"""
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = req_id
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        return response
