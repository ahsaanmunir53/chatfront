import json
from collections import deque
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from pydantic import BaseModel, ValidationError

from chatfront.ai.tools import Tool, ToolRegistry
from chatfront.core.errors import RateLimited
from chatfront.core.models import Tier
from chatfront.core.ratelimit import RateLimiter
from chatfront.jobs.cron import CronRegistry, Periodic
from chatfront.jobs.queue import JobContext, JobRegistry
from chatfront.modules.registry import Module, ModuleRegistry, core_modules
from chatfront.templates import BusinessTemplate, all_templates, get_template


def test_six_templates_and_their_modules_exist() -> None:
    templates = all_templates()
    assert sorted(templates) == [
        "clothing",
        "electronics",
        "food",
        "grocery",
        "services",
        "wholesale",
    ]
    registry = core_modules()
    for template in templates.values():
        assert set(template.modules) <= set(registry.modules)
        assert template.demo.tagline.ur
        assert template.agent_questions


def test_unknown_template() -> None:
    with pytest.raises(KeyError, match="unknown business template"):
        get_template("bakery-on-mars")


def test_template_file_must_match_id(tmp_path: Path) -> None:
    raw = json.loads((Path(__file__).parents[2] / "src/chatfront/templates/food.json").read_text())
    (tmp_path / "other.json").write_text(json.dumps(raw))
    all_templates.cache_clear()
    with pytest.raises(ValueError, match="file name"):
        all_templates(tmp_path)
    all_templates.cache_clear()


def test_template_rejects_unknown_fields() -> None:
    raw = json.loads((Path(__file__).parents[2] / "src/chatfront/templates/food.json").read_text())
    raw["surprise"] = True
    with pytest.raises(ValidationError):
        BusinessTemplate.model_validate(raw)


def test_tier_defaults() -> None:
    registry = core_modules()
    small = registry.defaults_for(Tier.SMALL)
    assert small == {"catalog", "orders", "payments", "books", "shield"}
    assert registry.defaults_for(Tier.MID) == small | {"delivery", "growth"}
    assert registry.defaults_for(Tier.LARGE) == small | {"delivery", "growth", "future"}
    assert "growth" in registry.defaults_for(Tier.SMALL, ["growth"])
    with pytest.raises(ValueError, match="unknown modules"):
        registry.defaults_for(Tier.SMALL, ["teleport"])


def test_install_wires_everything() -> None:
    class Args(BaseModel):
        x: int

    async def tool(ctx: object, params: Args) -> dict[str, object]:
        return {}

    async def job(ctx: JobContext) -> None:
        return None

    router = APIRouter()

    @router.get("/api/demo-module")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    registry = ModuleRegistry()
    registry.add(
        Module(
            "demo",
            Tier.SMALL,
            routers=[router],
            tools=[Tool("t", "", Args, tool, "demo")],
            jobs={"demo.job": job},
            periodic=[Periodic("demo", timedelta(hours=1), "demo.job")],
        )
    )
    with pytest.raises(ValueError, match="twice"):
        registry.add(Module("demo", Tier.SMALL))
    app, jobs, cron, tools = FastAPI(), JobRegistry(), CronRegistry(), ToolRegistry()
    registry.install(app, jobs, cron, tools)
    assert "demo.job" in jobs.handlers
    assert "demo" in cron.tasks
    assert "t" in tools.tools
    assert "/api/demo-module" in app.openapi()["paths"]


def test_rate_limiter() -> None:
    limiter = RateLimiter(per_minute=2)
    limiter.check("ip", now=0)
    limiter.check("ip", now=1)
    with pytest.raises(RateLimited):
        limiter.check("ip", now=2)
    limiter.check("ip", now=61)  # the window moved on
    limiter.check("other", now=2)
    for n in range(10_001):
        limiter.hits[f"k{n}"] = deque([0.0])
    limiter.check("fresh", now=200)
    assert len(limiter.hits) < 100
