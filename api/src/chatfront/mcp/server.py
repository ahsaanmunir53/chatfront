"""An MCP endpoint for each shop (feature 66): any AI assistant that speaks the Model Context
Protocol can find the shop's products, price a cart and start a checkout for its person.

The endpoint is /mcp/<shop>, over MCP's Streamable HTTP transport: JSON-RPC 2.0 messages
POSTed one at a time or in a batch, each answered with JSON. It keeps no session and offers
no event stream; a GET is refused. The tools are in mcp/tools.py; every one only reads.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from chatfront.core.errors import AppError
from chatfront.mcp.tools import TOOLS, Context

VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")  # newest first
SERVER = {"name": "chatfront", "title": "Chatfront shop", "version": "1.0.0"}
PARSE_ERROR, INVALID_REQUEST, NO_METHOD, INVALID_PARAMS, INTERNAL = (
    -32700,
    -32600,
    -32601,
    -32602,
    -32603,
)
MAX_BATCH = 20


def error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _initialize(ctx: Context, params: dict[str, Any]) -> dict[str, Any]:
    asked = params.get("protocolVersion")
    return {
        "protocolVersion": asked if asked in VERSIONS else VERSIONS[0],
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": SERVER,
        "instructions": (
            f"Tools for {ctx.tenant.name}, a shop on Chatfront. Prices and stock come from the "
            "shop's own records. Orders are placed by the person, through start_checkout's link."
        ),
    }


async def _call(ctx: Context, params: dict[str, Any]) -> dict[str, Any]:
    tool = TOOLS.get(str(params.get("name")))
    if tool is None:
        raise KeyError(str(params.get("name")))
    arguments = tool.params.model_validate(params.get("arguments") or {})
    output = await tool.run(ctx, arguments)
    return {
        "content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}],
        "structuredContent": output,
        "isError": "error" in output,
    }


async def handle(ctx: Context, message: Any) -> dict[str, Any] | None:
    """One JSON-RPC message: a response for a request, None for a notification."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return error(None, INVALID_REQUEST, "Not a JSON-RPC 2.0 message")
    method, request_id = message.get("method"), message.get("id")
    if "id" not in message:
        return None  # notifications, such as notifications/initialized, need no answer
    params = message.get("params") or {}
    if not isinstance(method, str) or not isinstance(params, dict):
        return error(request_id, INVALID_REQUEST, "A request needs a method and object params")
    try:
        if method == "initialize":
            return _result(request_id, _initialize(ctx, params))
        if method == "ping":
            return _result(request_id, {})
        if method == "tools/list":
            return _result(request_id, {"tools": [tool.listing() for tool in TOOLS.values()]})
        if method == "tools/call":
            return _result(request_id, await _call(ctx, params))
    except KeyError as exc:
        return error(request_id, INVALID_PARAMS, f"Unknown tool: {exc.args[0]}")
    except ValidationError as exc:
        return error(request_id, INVALID_PARAMS, exc.errors(include_url=False)[0]["msg"])
    except AppError as exc:
        return error(request_id, INVALID_PARAMS, exc.message)
    return error(request_id, NO_METHOD, f"Method not found: {method}")


async def handle_body(ctx: Context, body: Any) -> dict[str, Any] | list[dict[str, Any]] | None:
    """A message or a batch; None when there is nothing to answer."""
    if isinstance(body, list):
        if not body or len(body) > MAX_BATCH:
            return error(None, INVALID_REQUEST, f"A batch holds 1 to {MAX_BATCH} messages")
        answers = [answer for message in body if (answer := await handle(ctx, message))]
        return answers or None
    return await handle(ctx, body)
