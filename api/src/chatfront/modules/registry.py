"""Modules switch on per tenant. Each registers its routes, tools, jobs and dashboard panels here.

A tier is only a set of default modules (spec: Tiers); every tier runs the same code.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from fastapi import APIRouter, FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.ai.tools import Tool, ToolRegistry
from chatfront.core.models import TenantModule, Tier
from chatfront.jobs.cron import CronRegistry, Periodic
from chatfront.jobs.queue import Handler, JobRegistry


@dataclass
class Module:
    name: str
    default_tier: Tier
    routers: list[APIRouter] = field(default_factory=list)
    tools: list[Tool] = field(default_factory=list)
    jobs: dict[str, Handler] = field(default_factory=dict)
    periodic: list[Periodic] = field(default_factory=list)
    panels: list[str] = field(default_factory=list)


_TIER_ORDER = (Tier.SMALL, Tier.MID, Tier.LARGE)


@dataclass
class ModuleRegistry:
    modules: dict[str, Module] = field(default_factory=dict)

    def add(self, module: Module) -> None:
        if module.name in self.modules:
            raise ValueError(f"module registered twice: {module.name}")
        self.modules[module.name] = module

    def defaults_for(self, tier: Tier, extra: list[str] | None = None) -> set[str]:
        """Modules switched on for a new tenant: its tier's defaults plus its template's extras."""
        rank = _TIER_ORDER.index(tier)
        names = {m.name for m in self.modules.values() if _TIER_ORDER.index(m.default_tier) <= rank}
        unknown = set(extra or []) - set(self.modules)
        if unknown:
            raise ValueError(f"unknown modules: {sorted(unknown)}")
        return names | set(extra or [])

    def install(
        self, app: FastAPI, jobs: JobRegistry, cron: CronRegistry, tools: ToolRegistry
    ) -> None:
        for module in self.modules.values():
            for router in module.routers:
                app.include_router(router)
            for kind, handler in module.jobs.items():
                jobs.register(kind)(handler)
            for task in module.periodic:
                cron.add(task)
            for tool in module.tools:
                tools.add(tool)


async def enabled_modules(session: AsyncSession, tenant_id: uuid.UUID) -> set[str]:
    rows = await session.scalars(
        select(TenantModule.module).where(
            TenantModule.tenant_id == tenant_id, TenantModule.enabled.is_(True)
        )
    )
    return set(rows)


def core_modules() -> ModuleRegistry:
    """The eight modules from the spec. Phase 1 onward fills in their tools, jobs and routes."""
    registry = ModuleRegistry()
    for name, tier in (
        ("catalog", Tier.SMALL),
        ("orders", Tier.SMALL),
        ("payments", Tier.SMALL),
        ("books", Tier.SMALL),
        ("shield", Tier.SMALL),
        ("delivery", Tier.MID),
        ("growth", Tier.MID),
        ("future", Tier.LARGE),
    ):
        registry.add(Module(name, tier))
    return registry
