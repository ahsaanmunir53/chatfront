"""The order agent's evaluation set (feature 111): the cases are sound and the matcher holds a
real model to the script. The replay through the app is in tests/integration."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from chatfront.ai.types import ToolCall
from chatfront.catalog.models import Product
from chatfront.catalog.service import CatalogItem
from chatfront.channels.models import ChannelAccount
from chatfront.core.models import Tenant
from chatfront.eval import agent
from chatfront.eval.agent import Outcome, Summary, report, write_results
from chatfront.eval.cases import Call, Case, Shop, Step, check_calls, load_cases, satisfies
from chatfront.services import build_registries
from chatfront.templates import get_template

LANGUAGES = {"ur-Latn", "ur", "en", "pa-Arab", "ps", "sd"}


def _products(case: Case) -> list[tuple[str, dict[str, str]]]:
    named = [(line.product, line.options) for line in case.setup]
    if case.end.cart:
        named += [(line.product, line.options) for line in case.end.cart]
    for step in case.steps:
        named += [(call.product, call.options) for call in step.expect if call.product]
    return named


def test_a_hundred_conversations_of_every_kind_in_six_languages() -> None:
    cases = load_cases()
    assert len(cases) == 100
    assert len({case.id for case in cases}) == 100
    kinds: dict[str, int] = {}
    for case in cases:
        kinds[case.kind] = kinds.get(case.kind, 0) + 1
    assert set(kinds) == {
        "find", "add", "list", "offer", "checkout", "status",
        "cancel", "update", "handover", "injection", "view",
    }  # fmt: skip
    assert min(kinds.values()) >= 4
    assert {case.lang for case in cases} == LANGUAGES
    assert sum(case.lang == "ur-Latn" for case in cases) >= 30  # what most customers write


def test_cases_name_real_tools_products_and_options() -> None:
    modules = build_registries().modules.modules.values()
    tools = {tool.name for module in modules for tool in module.tools}
    for case in load_cases():
        demo = {p.name: p for p in get_template(case.shop).demo.products}
        for step in case.steps:
            assert step.say or step.tap is not None, case.id
            names = {call.tool for call in step.expect} | set(step.forbid)
            assert names <= tools, (case.id, names - tools)
        for name, options in _products(case):
            assert name in demo, (case.id, name)
            offered = {str(v) for variant in demo[name].variants for v in variant.options.values()}
            assert set(options.values()) <= offered, (case.id, options)


def _shop() -> Shop:
    product = Product(id=uuid.uuid4(), code="LWN7K", name="Printed lawn 3-piece")
    items = {product.name: CatalogItem(product, [])}
    return Shop(Tenant(), ChannelAccount(), items, order_code="Q8ZT2M")


def _call(name: str, **arguments: Any) -> ToolCall:
    return ToolCall("call_0", name, arguments)


@pytest.mark.parametrize(
    ("call", "wanted", "ok"),
    [
        (
            _call("add_to_cart", product_code="lwn7k ", quantity=2, options={"color": "rust"}),
            Call(tool="add_to_cart", product="Printed lawn 3-piece", quantity=2,
                 options={"colour": "Rust"}),
            True,
        ),
        (
            _call("add_to_cart", product_code="LWN7K", options={"colour": "Rust"}),
            Call(tool="add_to_cart", product="Printed lawn 3-piece", quantity=2),
            False,
        ),
        (
            _call("add_to_cart", product_code="LWN7K", quantity=1),
            Call(tool="add_to_cart", product="Printed lawn 3-piece", quantity=1),
            True,
        ),
        (_call("make_offer", product_code="LWN7K", price="3999.5"),
         Call(tool="make_offer", product="Printed lawn 3-piece", price=4000), True),
        (_call("make_offer", product_code="LWN7K", price=4400),
         Call(tool="make_offer", product="Printed lawn 3-piece", price=4000), False),
        (_call("cancel_order", order_code="q8zt2m"), Call(tool="cancel_order", order=True), True),
        (_call("cancel_order", order_code="AAAA11"), Call(tool="cancel_order", order=True), False),
        (_call("set_delivery_details", city="Lahore Cantt", address="x"),
         Call(tool="set_delivery_details", city="lahore"), True),
        (_call("checkout", payment_method="transfer"),
         Call(tool="checkout", payment_method="cod"), False),
        (_call("view_cart"), Call(tool="find_products"), False),
    ],
)  # fmt: skip
def test_a_real_call_must_do_what_the_script_asks(call: ToolCall, wanted: Call, ok: bool) -> None:
    assert satisfies(call, wanted, _shop()) is ok


def test_extra_calls_are_allowed_forbidden_ones_are_not() -> None:
    step = Step(
        say="2 rust lawn suits",
        expect=(Call(tool="add_to_cart", product="Printed lawn 3-piece", quantity=2),),
        forbid=("checkout",),
    )
    found = _call("find_products", query="lawn")
    added = _call("add_to_cart", product_code="LWN7K", quantity=2)
    assert check_calls(step, [found, added], _shop()) == []
    assert check_calls(step, [found, _call("checkout", payment_method="cod")], _shop()) == [
        "expected add_to_cart(product=Printed lawn 3-piece, quantity=2)",
        "called checkout",
    ]


def _summary(mode: str = "replay") -> Summary:
    return Summary(
        mode,
        [
            Outcome("add-01", "add", "ur-Latn", passed=True),
            Outcome("find-01", "find", "ps", problems=["step 1: expected find_products()"]),
        ],
    )


def test_results_file_and_reports(tmp_path: Path) -> None:
    target = tmp_path / "results.json"
    write_results(_summary(), target)
    write_results(_summary("live"), target)
    stored = json.loads(target.read_text(encoding="utf-8"))
    assert stored["replay"]["pass_rate"] == 0.5
    assert stored["live"]["by_kind"] == {"add": "1/1", "find": "0/1"}
    assert stored["live"]["by_language"]["ps"] == "0/1"
    text = report(_summary())
    assert text.startswith("Order agent evaluation (replay): 1/2 passed")
    assert "find-01: step 1: expected find_products()" in text
    markdown = report(_summary(), markdown=True)
    assert "| find | 0/1 |" in markdown
    assert "| ps | 0/1 |" in markdown


def test_command_line_fails_a_replay_that_is_not_perfect(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    async def canned(args: object, settings: object) -> Summary:
        return _summary()

    monkeypatch.setattr(agent, "_main", canned)
    monkeypatch.setattr(agent, "RESULTS_FILE", tmp_path / "results.json")
    with pytest.raises(SystemExit) as stopped:
        agent.main(["--markdown", "--write"])
    assert stopped.value.code == 1
    assert "### Order agent evaluation (replay): 1/2 passed" in capsys.readouterr().out
    assert json.loads((tmp_path / "results.json").read_text())["replay"]["passed"] == 1


def test_live_run_needs_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("BASETEN_API_KEY", "FIREWORKS_API_KEY", "MODAL_GLIMMER_BASE_URL"):
        monkeypatch.setenv(key, "")
    with pytest.raises(SystemExit, match="needs"):
        agent.main(["--live", "--kind", "add"])
