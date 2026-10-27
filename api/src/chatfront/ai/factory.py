"""Builds the AiClient from settings. Production never falls back to the mock."""

from __future__ import annotations

import httpx

from chatfront.ai.client import AiClient
from chatfront.ai.config import build_roles
from chatfront.ai.meter import db_meter
from chatfront.ai.mock import MockProvider
from chatfront.ai.provider import OpenAICompatibleProvider
from chatfront.core.db import Database
from chatfront.core.settings import Settings


def build_ai_client(settings: Settings, http: httpx.AsyncClient, db: Database) -> AiClient:
    roles = build_roles(settings)
    has_any_key = any(config.endpoints for config in roles.values())
    use_mock = settings.ai_mode == "mock" or (not has_any_key and settings.env != "prod")
    return AiClient(
        roles,
        live=OpenAICompatibleProvider(http),
        mock=MockProvider(),
        meter=db_meter(db),
        use_mock=use_mock,
        usd_to_pkr=settings.usd_to_pkr,
        record_dir=settings.ai_record_dir,
    )
