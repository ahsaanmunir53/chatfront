"""The only door to a model. Failover, reasoning per role, JSON validation and the cost meter."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from chatfront.ai.config import Endpoint, RoleConfig, cost_millipaisa
from chatfront.ai.mock import MOCK_ENDPOINT
from chatfront.ai.provider import Provider
from chatfront.ai.types import ChatMessage, ChatRequest, ChatResult, JsonSchema, Role
from chatfront.core.errors import AppError
from chatfront.core.logging import redact

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class ModelUnavailable(AppError):
    status_code = 503

    def __init__(self, role: Role, reason: str) -> None:
        super().__init__("model_unavailable", f"No model answered for {role.value}: {reason}")


@dataclass(frozen=True)
class CallRecord:
    tenant_id: uuid.UUID
    role: str
    provider: str
    model: str
    prompt: str | None
    input_tokens: int
    output_tokens: int
    cost_millipaisa: int
    latency_ms: int
    ok: bool
    error: str | None
    ref: tuple[str, str] | None


Meter = Callable[[CallRecord], Awaitable[None]]


class AiClient:
    def __init__(
        self,
        roles: dict[Role, RoleConfig],
        live: Provider,
        mock: Provider,
        meter: Meter,
        *,
        use_mock: bool,
        usd_to_pkr: Decimal,
        record_dir: Path | None = None,
    ) -> None:
        self.roles = roles
        self.live = live
        self.mock = mock
        self.meter = meter
        self.use_mock = use_mock
        self.usd_to_pkr = usd_to_pkr
        self.record_dir = record_dir

    def is_live(self, role: Role) -> bool:
        return not self.use_mock and bool(self.roles[role].endpoints)

    async def complete(self, req: ChatRequest) -> ChatResult:
        config = self.roles[req.role]
        effort = req.effort or config.effort
        max_tokens = req.max_tokens or config.max_tokens
        if self.use_mock:
            result = await self.mock.complete(MOCK_ENDPOINT, req, effort, max_tokens)
            await self._meter(req, MOCK_ENDPOINT, result, None)
            return result
        if not config.endpoints:
            raise ModelUnavailable(req.role, "no endpoint has a key")
        last_error = "unknown"
        for endpoint in config.endpoints:
            try:
                result = await self.live.complete(endpoint, req, effort, max_tokens)
            except AppError as exc:  # upstream failures and unreadable answers alike
                last_error = exc.message
                await self._meter(req, endpoint, None, exc.message)
                log.warning("%s failed for %s, trying the next endpoint", endpoint.name, req.role)
                continue
            await self._meter(req, endpoint, result, None)
            self._record(req, result)
            return result
        raise ModelUnavailable(req.role, last_error)

    async def complete_json(self, req: ChatRequest, model: type[T]) -> tuple[T, ChatResult]:
        """Ask for JSON matching a Pydantic model; one repair round if the first answer is off."""
        schema = JsonSchema(model.__name__, model.model_json_schema())
        req = replace(req, json_schema=schema)
        result = await self.complete(req)
        try:
            return model.model_validate_json(result.content or ""), result
        except ValidationError as exc:
            problems = exc.errors(include_url=False, include_input=False)
        repair = [
            *req.messages,
            ChatMessage("assistant", result.content or ""),
            ChatMessage(
                "user",
                "That JSON does not match the schema. Problems: "
                + json.dumps(problems, default=str)
                + ". Reply with corrected JSON only.",
            ),
        ]
        result = await self.complete(replace(req, messages=repair))
        try:
            return model.model_validate_json(result.content or ""), result
        except ValidationError as exc:
            raise AppError(
                "model_bad_json", f"{model.__name__}: {exc.error_count()} errors"
            ) from exc

    async def _meter(
        self, req: ChatRequest, endpoint: Endpoint, result: ChatResult | None, error: str | None
    ) -> None:
        usage_in = result.usage.input_tokens if result else 0
        usage_out = result.usage.output_tokens if result else 0
        record = CallRecord(
            tenant_id=req.tenant_id,
            role=req.role.value,
            provider=endpoint.name,
            model=endpoint.model,
            prompt=req.prompt,
            input_tokens=usage_in,
            output_tokens=usage_out,
            cost_millipaisa=cost_millipaisa(endpoint, usage_in, usage_out, self.usd_to_pkr),
            latency_ms=result.latency_ms if result else 0,
            ok=error is None,
            error=redact(error)[:500] if error else None,
            ref=req.ref,
        )
        try:
            await self.meter(record)
        except Exception:
            log.exception("could not record a model call")

    def _record(self, req: ChatRequest, result: ChatResult) -> None:
        """Save a live answer as a candidate mock fixture (AI_RECORD_DIR), for the eval set."""
        if self.record_dir is None:
            return
        last_user = next((m.text() for m in reversed(req.messages) if m.role == "user"), "")
        entry = {
            "prompt": req.prompt or "*",
            "role": req.role.value,
            "match": last_user[:80] or "*",
            "content": result.content,
            "tool_calls": [{"name": c.name, "arguments": c.arguments} for c in result.tool_calls],
        }
        self.record_dir.mkdir(parents=True, exist_ok=True)
        with (self.record_dir / "recorded.jsonl").open("a", encoding="utf-8") as out:
            out.write(json.dumps(entry, ensure_ascii=False) + "\n")
