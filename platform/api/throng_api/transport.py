"""Transport observability and stable agent error codes; never log request bodies."""

import json
import logging
import time
from contextvars import ContextVar
from uuid import UUID, uuid4

from fastapi import HTTPException

logger = logging.getLogger("throng.transport")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
db_wait = ContextVar("transport_db_wait", default=None)


class ProtocolError(HTTPException):
    def __init__(self, code, detail, status_code=409):
        super().__init__(status_code, detail)
        self.code = code


class RequestLog:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        try:
            request_id = str(UUID(headers.get(b"x-request-id", b"").decode("ascii")))
        except (ValueError, UnicodeError):
            request_id = str(uuid4())
        started, status = time.monotonic(), 500
        timing = [0.0, 0]
        token = db_wait.set(timing)

        async def response(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message = {
                    **message,
                    "headers": [
                        *message.get("headers", []),
                        (b"x-request-id", request_id.encode()),
                    ],
                }
            await send(message)

        try:
            await self.app(scope, receive, response)
        finally:
            db_wait.reset(token)
            logger.info(
                json.dumps(
                    {
                        "event": "http_request",
                        "request_id": request_id,
                        "method": scope["method"],
                        # Some API paths contain capabilities. Log the matched
                        # route template, never user-provided path segments.
                        "path": getattr(scope.get("route"), "path", "<unmatched>"),
                        "status": status,
                        "duration_ms": round((time.monotonic() - started) * 1000, 3),
                        "db_wait_ms": round(timing[0] * 1000, 3),
                        "db_writes": timing[1],
                    }
                )
            )
