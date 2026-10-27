"""OpenAI-compatible chat completions: Baseten, Fireworks, a Modal vLLM server, the Meta Model API.

Media goes as image_url, audio_url and video_url parts, which is how Baseten and vLLM take
Muse Spark Open's image, audio and video input.
"""

from __future__ import annotations

import json
import time
from typing import Any, Protocol

import httpx

from chatfront.ai.config import Endpoint
from chatfront.ai.types import (
    AudioPart,
    ChatMessage,
    ChatRequest,
    ChatResult,
    Effort,
    ImagePart,
    Part,
    TextPart,
    ToolCall,
    Usage,
    VideoPart,
)
from chatfront.core.errors import AppError
from chatfront.core.http import request as http_request


class Provider(Protocol):
    async def complete(
        self, endpoint: Endpoint, req: ChatRequest, effort: Effort, max_tokens: int
    ) -> ChatResult: ...


def _part(part: Part) -> dict[str, Any]:
    match part:
        case TextPart(text=text):
            return {"type": "text", "text": text}
        case ImagePart(url=url):
            return {"type": "image_url", "image_url": {"url": url}}
        case AudioPart(url=url):
            return {"type": "audio_url", "audio_url": {"url": url}}
        case VideoPart(url=url):
            return {"type": "video_url", "video_url": {"url": url}}
    raise AssertionError(part)  # pragma: no cover


def serialise_message(message: ChatMessage) -> dict[str, Any]:
    out: dict[str, Any] = {"role": message.role}
    if isinstance(message.content, list):
        out["content"] = [_part(part) for part in message.content]
    else:
        out["content"] = message.content
    if message.tool_calls:
        out["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in message.tool_calls
        ]
    if message.tool_call_id:
        out["tool_call_id"] = message.tool_call_id
    return out


def build_payload(
    endpoint: Endpoint, req: ChatRequest, effort: Effort, max_tokens: int
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": endpoint.model,
        "messages": [serialise_message(m) for m in req.messages],
        "max_tokens": max_tokens,
        "reasoning_effort": effort.value,
    }
    if req.tools:
        payload["tools"] = req.tools
        payload["tool_choice"] = "auto"
    if req.json_schema:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": req.json_schema.name,
                "schema": req.json_schema.schema,
                "strict": True,
            },
        }
    return payload


def parse_response(data: dict[str, Any], endpoint: Endpoint, latency_ms: int) -> ChatResult:
    try:
        choice = data["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AppError("model_bad_response", f"{endpoint.name} returned no choices") from exc
    calls: list[ToolCall] = []
    for raw in message.get("tool_calls") or []:
        function = raw.get("function", {})
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            arguments = {"_unparsed": function.get("arguments")}
        calls.append(ToolCall(raw.get("id", ""), function.get("name", ""), arguments))
    usage = data.get("usage") or {}
    return ChatResult(
        content=message.get("content"),
        tool_calls=tuple(calls),
        usage=Usage(int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))),
        provider=endpoint.name,
        model=endpoint.model,
        finish_reason=choice.get("finish_reason"),
        latency_ms=latency_ms,
    )


class OpenAICompatibleProvider:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def complete(
        self, endpoint: Endpoint, req: ChatRequest, effort: Effort, max_tokens: int
    ) -> ChatResult:
        headers = {"Content-Type": "application/json"}
        if endpoint.api_key:
            headers["Authorization"] = f"Bearer {endpoint.api_key}"
        started = time.monotonic()
        response = await http_request(
            self.client,
            "POST",
            endpoint.base_url.rstrip("/") + "/chat/completions",
            service=endpoint.name,
            json=build_payload(endpoint, req, effort, max_tokens),
            headers=headers,
            retries=1,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        return parse_response(response.json(), endpoint, latency_ms)
