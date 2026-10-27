"""The shapes every model call uses, independent of which provider serves it."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal


class Role(StrEnum):
    """What a call is for; config maps each role to endpoints and a reasoning setting."""

    CHAT = "chat"
    VISION = "vision"
    AUDIO = "audio"
    VIDEO = "video"
    WATCHER = "watcher"
    SIMULATOR = "simulator"


class Effort(StrEnum):
    """Muse Spark Open reasoning effort. It cannot be switched off."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


@dataclass(frozen=True)
class TextPart:
    text: str


@dataclass(frozen=True)
class ImagePart:
    url: str


@dataclass(frozen=True)
class AudioPart:
    url: str


@dataclass(frozen=True)
class VideoPart:
    url: str


Part = TextPart | ImagePart | AudioPart | VideoPart


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[Part] | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None

    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        if self.content is None:
            return ""
        return " ".join(part.text for part in self.content if isinstance(part, TextPart))


@dataclass(frozen=True)
class JsonSchema:
    name: str
    schema: dict[str, Any]


@dataclass(frozen=True)
class ChatRequest:
    role: Role
    messages: list[ChatMessage]
    tenant_id: uuid.UUID
    prompt: str | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    json_schema: JsonSchema | None = None
    effort: Effort | None = None
    max_tokens: int | None = None
    ref: tuple[str, str] | None = None


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class ChatResult:
    content: str | None
    tool_calls: tuple[ToolCall, ...]
    usage: Usage
    provider: str
    model: str
    finish_reason: str | None = None
    latency_ms: int = 0
