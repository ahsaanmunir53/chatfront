"""One error type for expected failures, rendered the same way by every route."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    """An expected failure with a stable code that clients and tests can rely on."""

    status_code = 400

    def __init__(self, code: str, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        if status_code is not None:
            self.status_code = status_code

    def to_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message}}


class NotFound(AppError):
    status_code = 404

    def __init__(self, what: str) -> None:
        super().__init__("not_found", f"{what} not found")


class Forbidden(AppError):
    status_code = 403

    def __init__(self, message: str = "Not allowed") -> None:
        super().__init__("forbidden", message)


class Unauthorized(AppError):
    status_code = 401

    def __init__(self, message: str = "Sign in required") -> None:
        super().__init__("unauthorized", message)


class RateLimited(AppError):
    status_code = 429

    def __init__(self) -> None:
        super().__init__("rate_limited", "Too many requests, try again in a minute")


def install_error_handlers(app: FastAPI) -> None:
    async def handle(_: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, AppError)
        headers = {"Retry-After": "60"} if exc.status_code == 429 else None
        return JSONResponse(exc.to_dict(), status_code=exc.status_code, headers=headers)

    app.add_exception_handler(AppError, handle)
