"""
core/responses.py — Centralized HTTP response builders.

Eliminates duplicated JSONResponse(content={"error": ...}, status_code=...)
patterns across all routers (~50+ call sites).
"""

from __future__ import annotations

from fastapi.responses import JSONResponse


def error_response(msg: str, status: int = 500) -> JSONResponse:
    """Return a JSON error response with a standardized shape."""
    return JSONResponse(content={"error": msg}, status_code=status)


def success_response(data: dict, status: int = 200) -> JSONResponse:
    """Return a JSON success response."""
    return JSONResponse(content=data, status_code=status)
