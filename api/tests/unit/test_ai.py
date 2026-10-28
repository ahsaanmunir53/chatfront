import json
import uuid
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pydantic import BaseModel, SecretStr

from chatfront.ai.client import AiClient, CallRecord, ModelUnavailable
from chatfront.ai.config import (
    JOB_ROUTES,
    SPARK_OPEN_PRICE,
    Endpoint,
    RoleConfig,
    build_roles,
    cost_millipaisa,
    job_of,
    route,
)
from chatfront.ai.factory import build_ai_client
from chatfront.ai.mock import Fixture, MockMiss, MockProvider, load_fixtures, tool_rounds
from chatfront.ai.prompts import PROMPTS_DIR, load_prompt
from chatfront.ai.provider import OpenAICompatibleProvider, build_payload, parse_response
from chatfront.ai.tools import Tool, ToolRegistry
from chatfront.ai.types import (
    AudioPart,
    ChatMessage,
    ChatRequest,
    Effort,
    ImagePart,
    Role,
    TextPart,
    ToolCall,
    VideoPart,
)
from chatfront.core.errors import AppError
from chatfront.core.settings import Settings

TENANT = uuid.UUID(int=1)
BASETEN = Endpoint(
    "baseten", "https://b.test/v1", "kb", "meta-models/muse-spark-open-1026", *SPARK_OPEN_PRICE
)
FIREWORKS = Endpoint("fireworks", "https://f.test/v1", "kf", "fw/muse", *SPARK_OPEN_PRICE)


def chat(text: str = "salam", **kwargs: object) -> ChatRequest:
    return ChatRequest(
        role=Role.CHAT,
        messages=[ChatMessage("user", text)],
        tenant_id=TENANT,
        prompt="shop_assistant",
        **kwargs,  # type: ignore[arg-type]
    )


def ok_body(content: str = "hi", **extra: object) -> dict[str, object]:
    message = {"role": "assistant", "content": content, **extra}
    return {
        "choices": [{"message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }


class Meter:
    def __init__(self) -> None:
        self.records: list[CallRecord] = []

    async def __call__(self, record: CallRecord) -> None:
        self.records.append(record)


def client_for(
    *endpoints: Endpoint,
    mock: MockProvider | None = None,
    use_mock: bool = False,
    record_dir: Path | None = None,
    http: httpx.AsyncClient | None = None,
) -> tuple[AiClient, Meter]:
    meter = Meter()
    roles = {role: RoleConfig(endpoints, Effort.LOW, 1000) for role in Role}
    client = AiClient(
        roles,
        OpenAICompatibleProvider(http or httpx.AsyncClient()),
        mock or MockProvider([]),
        meter,
        use_mock=use_mock,
        usd_to_pkr=Decimal(280),
        record_dir=record_dir,
    )
    return client, meter


class TestConfig:
    def test_no_keys_means_no_endpoints(self) -> None:
        roles = build_roles(Settings(baseten_api_key=None, fireworks_api_key=None))
        assert all(not config.endpoints for config in roles.values())

    def test_chains(self) -> None:
        settings = Settings(
            baseten_api_key=SecretStr("b"),
            fireworks_api_key=SecretStr("f"),
            modal_glimmer_base_url="https://m.test/v1",
            meta_model_api_enabled=True,
            meta_model_api_key=SecretStr("m"),
            meta_model_api_base_url="https://meta.test/v1",
        )
        roles = build_roles(settings)
        assert [e.name for e in roles[Role.CHAT].endpoints] == ["meta", "baseten", "fireworks"]
        assert [e.name for e in roles[Role.WATCHER].endpoints][:2] == ["modal", "meta"]
        assert roles[Role.CHAT].effort is Effort.LOW
        assert roles[Role.VISION].effort is Effort.MEDIUM

    def test_baseten_uses_the_hackathon_model_unless_configured(self) -> None:
        default = build_roles(Settings(baseten_api_key=SecretStr("b")))
        assert default[Role.CHAT].endpoints[0].model == "meta-models/muse-spark-open-1026"
        custom = Settings(baseten_api_key=SecretStr("b"), spark_model_baseten="meta-models/other")
        assert build_roles(custom)[Role.CHAT].endpoints[0].model == "meta-models/other"

    def test_meta_needs_explicit_url(self) -> None:
        settings = Settings(meta_model_api_enabled=True, meta_model_api_key=SecretStr("m"))
        assert build_roles(settings)[Role.CHAT].endpoints == ()

    def test_cost(self) -> None:
        # 1M input tokens at $1.25 and Rs 280/$ = Rs 350 = 35,000 paisa
        assert cost_millipaisa(BASETEN, 1_000_000, 0, Decimal(280)) == 35_000_000
        assert cost_millipaisa(BASETEN, 1000, 200, Decimal(280)) == 58_800  # Rs 0.588


class TestRouting:
    """Reasoning routing (feature 113): light for chat, deepest for payment forensics."""

    def test_jobs_take_their_own_setting(self) -> None:
        vision = RoleConfig((), Effort.MEDIUM, 8192)
        chat_role = RoleConfig((), Effort.LOW, 4096)
        assert route(chat_role, "order_agent@v7") == (Effort.LOW, 4096)
        assert route(vision, "payment_screenshot@v2") == (Effort.XHIGH, 16384)
        assert route(vision, "product_draft@v3") == (Effort.MEDIUM, 8192)
        assert route(chat_role, "payment_evidence@v1") == (Effort.HIGH, 4096)
        assert route(vision, "handover_summary@v1") == (Effort.LOW, 8192)  # media needs room
        assert route(vision, "something_new@v1") == (Effort.MEDIUM, 8192)
        assert route(chat_role, None) == (Effort.LOW, 4096)
        assert job_of("translate@v2") == "translate"

    def test_every_prompt_is_routed_and_forensics_is_deepest(self) -> None:
        prompts = {path.stem for path in PROMPTS_DIR.glob("*.md")}
        assert prompts == set(JOB_ROUTES)
        deepest = max(JOB_ROUTES.values(), key=lambda pair: list(Effort).index(pair[0]))
        assert JOB_ROUTES["payment_screenshot"] == deepest

    async def test_the_call_carries_and_records_its_setting(self) -> None:
        mock = MockProvider([Fixture("*", "*", "*", content="ok")])
        client, meter = client_for(use_mock=True, mock=mock)
        await client.complete(replace(chat(), prompt="payment_screenshot@v2"))
        await client.complete(replace(chat(), prompt="order_agent@v7", effort=Effort.HIGH))
        assert [r.effort for r in meter.records] == ["xhigh", "high"]
        assert mock.requests[0].effort is Effort.XHIGH
        assert mock.requests[0].max_tokens == 16384


class TestProvider:
    def test_payload_carries_media_tools_schema_and_effort(self) -> None:
        req = ChatRequest(
            role=Role.VISION,
            messages=[
                ChatMessage("system", "be brief"),
                ChatMessage(
                    "user",
                    [
                        TextPart("what is this"),
                        ImagePart("https://i"),
                        AudioPart("https://a"),
                        VideoPart("https://v"),
                    ],
                ),
                ChatMessage("assistant", None, tool_calls=(ToolCall("c1", "f", {"x": 1}),)),
                ChatMessage("tool", '{"ok":true}', tool_call_id="c1"),
            ],
            tenant_id=TENANT,
            tools=[{"type": "function", "function": {"name": "f"}}],
        )
        payload = build_payload(BASETEN, req, Effort.XHIGH, 512)
        parts = payload["messages"][1]["content"]
        assert [p["type"] for p in parts] == ["text", "image_url", "audio_url", "video_url"]
        assert payload["reasoning_effort"] == "xhigh"
        assert payload["max_tokens"] == 512
        assert payload["tool_choice"] == "auto"
        assert payload["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{"x": 1}'
        assert payload["messages"][3]["tool_call_id"] == "c1"
        assert "response_format" not in payload

    def test_parse_tool_calls(self) -> None:
        body = ok_body(
            None,
            tool_calls=[  # type: ignore[arg-type]
                {"id": "a", "function": {"name": "check_stock", "arguments": '{"sku":"L-1"}'}},
                {"id": "b", "function": {"name": "broken", "arguments": "{nope"}},
            ],
        )
        result = parse_response(body, BASETEN, 10)
        assert result.tool_calls[0] == ToolCall("a", "check_stock", {"sku": "L-1"})
        assert result.tool_calls[1].arguments == {"_unparsed": "{nope"}
        assert result.usage.input_tokens == 100

    def test_parse_rejects_empty(self) -> None:
        with pytest.raises(AppError, match="no choices"):
            parse_response({"choices": []}, BASETEN, 0)

    @respx.mock
    async def test_sends_bearer_and_json(self) -> None:
        route = respx.post("https://b.test/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=ok_body("salam"))
        )
        async with httpx.AsyncClient() as http:
            result = await OpenAICompatibleProvider(http).complete(BASETEN, chat(), Effort.LOW, 100)
        sent = route.calls[0].request
        assert sent.headers["Authorization"] == "Bearer kb"
        assert json.loads(sent.content)["model"] == "meta-models/muse-spark-open-1026"
        assert result.content == "salam"
        assert result.provider == "baseten"


class TestMock:
    def test_specific_beats_wildcard_and_json(self) -> None:
        mock = MockProvider(
            [
                Fixture("*", "*", "*", content="any"),
                Fixture("shop_assistant", "chat", "kitne", json={"price": 1}),
            ]
        )
        assert mock.find(chat("yeh kitne ka hai")).json == {"price": 1}
        assert mock.find(chat("hello")).content == "any"

    def test_miss(self) -> None:
        with pytest.raises(MockMiss):
            MockProvider([]).find(chat())

    def test_a_tool_loop_moves_through_its_steps(self) -> None:
        mock = MockProvider(
            [
                Fixture("*", "*", "lawn", tool_calls=[{"name": "find_products"}]),
                Fixture("*", "*", "lawn", step=1, tool_calls=[{"name": "add_to_cart"}]),
                Fixture("*", "*", "lawn", content="Added."),
            ]
        )
        first = chat("lawn chahiye")
        assert [c["name"] for c in mock.find(first).tool_calls] == ["find_products"]
        call = ToolCall("call_0", "find_products", {})
        looped = [
            *first.messages,
            ChatMessage("assistant", None, (call,)),
            ChatMessage("tool", "{}", tool_call_id="call_0"),
        ]
        second = replace(first, messages=looped)
        assert tool_rounds(second) == 1
        assert [c["name"] for c in mock.find(second).tool_calls] == ["add_to_cart"]
        third = replace(second, messages=[*looped, ChatMessage("assistant", None, (call,))])
        assert mock.find(third).content == "Added."  # tool fixtures never repeat forever

    def test_shipped_fixtures_load(self) -> None:
        assert any(f.prompt == "shop_assistant" for f in load_fixtures())


class TestClient:
    @respx.mock
    async def test_fails_over_and_meters_both(self) -> None:
        respx.post("https://b.test/v1/chat/completions").mock(return_value=httpx.Response(503))
        respx.post("https://f.test/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=ok_body("from fireworks"))
        )
        client, meter = client_for(BASETEN, FIREWORKS)
        result = await client.complete(chat(ref=("order", "CF-1")))
        assert result.content == "from fireworks"
        assert [(r.provider, r.ok) for r in meter.records] == [
            ("baseten", False),
            ("fireworks", True),
        ]
        assert meter.records[1].cost_millipaisa > 0
        assert meter.records[1].ref == ("order", "CF-1")

    @respx.mock
    async def test_all_down(self) -> None:
        respx.post("https://b.test/v1/chat/completions").mock(return_value=httpx.Response(401))
        client, _ = client_for(BASETEN)
        with pytest.raises(ModelUnavailable):
            await client.complete(chat())

    async def test_no_endpoints(self) -> None:
        client, _ = client_for()
        assert not client.is_live(Role.CHAT)
        with pytest.raises(ModelUnavailable, match="no endpoint"):
            await client.complete(chat())

    async def test_mock_path_is_metered(self) -> None:
        client, meter = client_for(
            BASETEN, use_mock=True, mock=MockProvider([Fixture("*", "*", "*", content="m")])
        )
        assert (await client.complete(chat())).content == "m"
        assert meter.records[0].provider == "mock"
        assert not client.is_live(Role.CHAT)

    async def test_json_with_one_repair(self) -> None:
        class Price(BaseModel):
            amount: int

        mock = MockProvider(
            [
                Fixture("*", "*", "*", content='{"amount": "lots"}'),
                Fixture("*", "*", "corrected", content='{"amount": 2500}'),
            ]
        )
        client, _ = client_for(use_mock=True, mock=mock)
        value, _ = await client.complete_json(chat(), Price)
        assert value.amount == 2500
        assert mock.requests[0].json_schema is not None
        assert mock.requests[0].json_schema.name == "Price"

    async def test_json_gives_up(self) -> None:
        class Price(BaseModel):
            amount: int

        client, _ = client_for(
            use_mock=True, mock=MockProvider([Fixture("*", "*", "*", content="no")])
        )
        with pytest.raises(AppError, match="Price"):
            await client.complete_json(chat(), Price)

    @respx.mock
    async def test_records_live_answers(self, tmp_path: Path) -> None:
        respx.post("https://b.test/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=ok_body("ji"))
        )
        client, _ = client_for(BASETEN, record_dir=tmp_path)
        await client.complete(chat("aap ki dukaan kab khulti hai"))
        line = json.loads((tmp_path / "recorded.jsonl").read_text().splitlines()[0])
        assert line["match"] == "aap ki dukaan kab khulti hai"
        assert line["content"] == "ji"


def test_factory_never_mocks_in_production() -> None:
    no_db: Any = None
    prod = build_ai_client(Settings(env="prod", ai_mode="auto"), httpx.AsyncClient(), no_db)
    assert not prod.use_mock
    dev = build_ai_client(Settings(env="dev", ai_mode="auto"), httpx.AsyncClient(), no_db)
    assert dev.use_mock


def test_prompt_file() -> None:
    prompt = load_prompt("shop_assistant")
    assert prompt.ref == "shop_assistant@v2"
    text = prompt.render(
        shop_name="Zara",
        business_type="clothing",
        country="Pakistan",
        channel="WhatsApp",
        reply_language="Pashto, written in Pashto script",
        shop_details="Open 10 to 8",
    )
    assert "Zara" in text
    assert "Reply in Pashto, written in Pashto script." in text
    assert "Never invent a price" in text


def test_prompt_needs_version(tmp_path: Path) -> None:
    (tmp_path / "bad.md").write_text("hello\n\nbody")
    with pytest.raises(ValueError, match="version"):
        load_prompt("bad", tmp_path)


class TestTools:
    class Args(BaseModel):
        sku: str

    @staticmethod
    async def handler(ctx: object, params: "TestTools.Args") -> dict[str, object]:
        return {"sku": params.sku, "in_stock": 3}

    def registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.add(Tool("check_stock", "Stock for a SKU", self.Args, self.handler, "catalog"))
        return registry

    async def test_runs_validates_and_respects_modules(self) -> None:
        registry = self.registry()
        assert registry.specs({"catalog"})[0]["function"]["name"] == "check_stock"
        assert registry.specs({"books"}) == []
        good = await registry.run(ToolCall("1", "check_stock", {"sku": "L-1"}), None, {"catalog"})
        assert good == {"sku": "L-1", "in_stock": 3}
        bad = await registry.run(ToolCall("1", "check_stock", {}), None, {"catalog"})
        assert bad["error"] == "invalid arguments"
        off = await registry.run(ToolCall("1", "check_stock", {"sku": "x"}), None, {"books"})
        assert off == {"error": "unknown tool check_stock"}

    def test_duplicate(self) -> None:
        registry = self.registry()
        with pytest.raises(ValueError, match="twice"):
            registry.add(Tool("check_stock", "", self.Args, self.handler, "catalog"))
