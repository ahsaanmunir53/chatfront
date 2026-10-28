"""The MCP endpoint's web side (feature 66): POST /mcp/<shop> takes JSON-RPC messages."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from chatfront.mcp.server import PARSE_ERROR, error, handle_body
from chatfront.mcp.tools import Context
from chatfront.routes.public import shop_by_slug
from chatfront.services import Services, get_services

router = APIRouter()
ServicesDep = Annotated[Services, Depends(get_services)]
MAX_BODY = 64 * 1024


@router.post("/mcp/{slug}", include_in_schema=False)
async def mcp(slug: str, request: Request, services: ServicesDep) -> Response:
    client = request.client.host if request.client else "unknown"
    services.limiter.check(f"mcp:{client}")
    tenant = await shop_by_slug(services, slug)
    raw = await request.body()
    try:
        body = json.loads(raw[:MAX_BODY]) if len(raw) <= MAX_BODY else None
    except ValueError:
        body = None
    if body is None:
        return JSONResponse(error(None, PARSE_ERROR, "Send JSON-RPC as JSON"), status_code=400)
    answer = await handle_body(Context(services, tenant), body)
    if answer is None:
        return Response(status_code=202)
    return JSONResponse(answer)


@router.get("/mcp/{slug}", include_in_schema=False)
async def mcp_stream(slug: str) -> Response:
    """No event stream: this endpoint answers each POST with JSON."""
    del slug
    return Response(status_code=405, headers={"Allow": "POST"})
