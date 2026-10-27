"""The mock provider answers from recorded fixtures, so everything runs with no keys.

Fixtures live in ai/fixtures/*.json. Each entry names a prompt and role ("*" for any) and a
`match` substring of the last user message ("*" for any); specific matches win over "*".
Real responses can be captured with AI_RECORD_DIR and promoted into fixtures.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from chatfront.ai.config import Endpoint
from chatfront.ai.types import ChatRequest, ChatResult, Effort, ToolCall, Usage
from chatfront.core.errors import AppError

FIXTURES_DIR = Path(__file__).parent / "fixtures"
MOCK_ENDPOINT = Endpoint("mock", "", None, "mock", Decimal(0), Decimal(0))


class MockMiss(AppError):
    def __init__(self, req: ChatRequest) -> None:
        super().__init__(
            "mock_fixture_missing",
            f"No mock fixture for prompt={req.prompt!r} role={req.role.value!r}",
            status_code=500,
        )


@dataclass(frozen=True)
class Fixture:
    prompt: str
    role: str
    match: str
    content: str | None = None
    json: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    def matches(self, req: ChatRequest, last_user_text: str) -> bool:
        prompt_name = (req.prompt or "").split("@")[0]  # fixtures hold across prompt versions
        return (
            self.prompt in ("*", prompt_name)
            and self.role in ("*", req.role.value)
            and (self.match == "*" or self.match.lower() in last_user_text.lower())
        )


def load_fixtures(directory: Path = FIXTURES_DIR) -> list[Fixture]:
    fixtures: list[Fixture] = []
    for path in sorted(directory.glob("*.json")):
        for raw in json.loads(path.read_text(encoding="utf-8")):
            fixtures.append(Fixture(**raw))
    return fixtures


def _last_user_text(req: ChatRequest) -> str:
    for message in reversed(req.messages):
        if message.role == "user":
            return message.text()
    return ""


class MockProvider:
    def __init__(self, fixtures: list[Fixture] | None = None) -> None:
        self.fixtures = fixtures if fixtures is not None else load_fixtures()
        self.requests: list[ChatRequest] = []

    def find(self, req: ChatRequest) -> Fixture:
        text = _last_user_text(req)
        candidates = [f for f in self.fixtures if f.matches(req, text)]
        candidates.sort(key=lambda f: f.match == "*")
        if not candidates:
            raise MockMiss(req)
        return candidates[0]

    async def complete(
        self, endpoint: Endpoint, req: ChatRequest, effort: Effort, max_tokens: int
    ) -> ChatResult:
        del endpoint, effort, max_tokens  # same signature as the live provider; unused here
        self.requests.append(req)
        fixture = self.find(req)
        content = json.dumps(fixture.json, ensure_ascii=False) if fixture.json else fixture.content
        calls = tuple(
            ToolCall(f"call_{index}", raw["name"], raw.get("arguments", {}))
            for index, raw in enumerate(fixture.tool_calls)
        )
        prompt_chars = sum(len(m.text()) for m in req.messages)
        return ChatResult(
            content=content,
            tool_calls=calls,
            usage=Usage(prompt_chars // 4, len(content or "") // 4),
            provider="mock",
            model="mock",
            finish_reason="tool_calls" if calls else "stop",
        )
