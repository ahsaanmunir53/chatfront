"""A busy day from the command line (feature 110): simulated customers against a fresh copy of
a demo shop, then a report of how their chats ended.

    python -m chatfront.sim                          # the clothing shop, 50 customers, replayed
    python -m chatfront.sim --shop grocery --customers 20
    python -m chatfront.sim --live                   # Glimmer plays them, Spark Open answers
    ... --keep                                       # leave the copy, to look at in the console

It needs a migrated and seeded database at DATABASE_URL (`python -m chatfront.seed`). The
customers move as fast as the shop answers; in the console they arrive a few seconds apart.
A replay is scripted, so a customer left stuck there means something broke.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from collections.abc import Sequence

from chatfront.ai import meter
from chatfront.core.clock import utcnow
from chatfront.core.models import Tenant
from chatfront.eval.agent import eval_settings
from chatfront.main import create_app
from chatfront.owner.sandbox import copy_demo_shop, remove_shops
from chatfront.services import Services
from chatfront.sim import runner
from chatfront.sim.models import Mode
from chatfront.templates import get_template

TIMEOUT_SECONDS = 3600


async def _drive(services: Services, tenant: Tenant) -> runner.Progress:
    """Run the queue until every customer's chat has ended."""
    assert services.worker is not None
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        while await services.worker.run_once():
            pass
        async with services.db.tenant(tenant.id) as session:
            progress = await runner.latest(session)
        assert progress is not None
        if progress.run.finished_at is not None:
            return progress
        await asyncio.sleep(0.05)
    raise SystemExit("The busy day did not finish within the hour")


async def busy_day(
    services: Services, template: str, customers: int, *, live: bool, keep: bool
) -> tuple[str, runner.Progress]:
    tenant, _ = await copy_demo_shop(
        services.db,
        services.registries.modules,
        get_template(template).demo.slug,
        services.settings.secret_key.get_secret_value(),
    )
    try:
        mode = Mode.LIVE if live else Mode.REPLAY
        run = await runner.start(services, tenant, None, customers, mode=mode)
        progress = await _drive(services, tenant)
        async with services.db.tenant(tenant.id) as session:
            used = await meter.in_period(session, run.started_at, utcnow())
    finally:
        if not keep:
            await remove_shops(services, [tenant.id])
    return report(tenant, progress, used, kept=keep), progress


def report(tenant: Tenant, progress: runner.Progress, used: meter.Usage, *, kept: bool) -> str:
    run = progress.run
    lines = [
        f"Busy day at {tenant.name} ({run.mode}): {run.customers} customers sent "
        f"{progress.messages} messages",
        f"  ordered    {progress.ordered}",
        f"  handover   {progress.handover}",
        f"  left       {progress.left}",
        f"  stuck      {progress.stuck}",
        f"  model calls {used.calls} ({used.failed} failed), "
        f"{used.tokens} tokens, Rs {used.cost_millipaisa / 100_000:.2f}",
    ]
    if kept:
        lines.append(f"  the copy is kept: /s/{tenant.slug}")
    return "\n".join(lines)


async def _main(args: argparse.Namespace) -> tuple[str, runner.Progress]:
    settings = eval_settings(args.live).model_copy(update={"sim_pace_seconds": 0.0})
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        services: Services = app.state.services
        return await busy_day(services, args.shop, args.customers, live=args.live, keep=args.keep)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--shop", default="clothing", help="the demo shop's business type")
    parser.add_argument("--customers", type=int, default=runner.MAX_CUSTOMERS)
    parser.add_argument("--live", action="store_true", help="real models play and answer")
    parser.add_argument("--keep", action="store_true", help="keep the shop copy afterwards")
    args = parser.parse_args(argv)
    text, progress = asyncio.run(_main(args))
    print(text)
    if not args.live and progress.stuck:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
