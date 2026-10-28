"""The order agent's evaluation set replayed through the whole app (feature 111): one
conversation of each kind, and a shop that ends wrong is caught."""

from __future__ import annotations

import httpx
import pytest

from chatfront.eval import agent
from chatfront.eval.agent import evaluate
from chatfront.eval.cases import load_cases
from chatfront.services import Services


async def test_replay_passes_one_conversation_of_each_kind(
    app: tuple[httpx.AsyncClient, Services],
) -> None:
    _, services = app
    first: dict[str, str] = {}
    for case in load_cases():
        first.setdefault(case.kind, case.id)
    summary = await evaluate(services, live=False, ids=list(first.values()))
    assert summary.total == len(first)
    assert summary.passed == summary.total, [o.problems for o in summary.outcomes if o.problems]
    tapped = next(o for o in summary.outcomes if o.kind == "checkout")
    assert tapped.calls == [["add_to_cart"], ["set_delivery_details"], ["checkout"], []]


async def test_replay_notices_a_shop_that_ends_wrong(
    app: tuple[httpx.AsyncClient, Services], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, services = app
    case = next(c for c in load_cases() if c.id == "add-01")
    wrong = case.model_copy(
        update={"end": case.end.model_copy(update={"cart": (), "handover": True})}
    )
    monkeypatch.setattr(agent, "load_cases", lambda: [wrong])
    summary = await evaluate(services, live=False)
    assert summary.passed == 0
    assert summary.outcomes[0].problems == [
        "cart holds 2 x Printed lawn 3-piece Rust",
        "not handed over",
    ]
