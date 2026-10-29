"""Which endpoint serves which role, how hard it reasons for each job, and what it costs.

Spark Open fails over Baseten -> Modal -> Fireworks (feature 114). Baseten and Fireworks bill
per token; a Modal deployment bills GPU time from credits, so its calls count no token cost.
Spark Open needs a node of eight large GPUs, so its Modal endpoint is optional and only set
while one runs. Glimmer runs on our own Modal deployment (deploy/modal_glimmer.py); the
watcher and simulator fall back to Spark Open when Modal is absent. The Meta Model API sits
first in every chain only when META_MODEL_API_ENABLED is set.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from chatfront.ai.types import Effort, Role
from chatfront.core.settings import Settings


@dataclass(frozen=True)
class Endpoint:
    name: str
    base_url: str
    api_key: str | None
    model: str
    usd_per_m_input: Decimal
    usd_per_m_output: Decimal


@dataclass(frozen=True)
class RoleConfig:
    endpoints: tuple[Endpoint, ...]
    effort: Effort
    max_tokens: int


SPARK_OPEN_PRICE = (Decimal("1.25"), Decimal("4.25"))

_ROLE_SETTINGS: dict[Role, tuple[Effort, int, bool]] = {
    # role: (effort, max_tokens incl. reasoning, prefers Glimmer)
    Role.CHAT: (Effort.LOW, 4096, False),
    Role.VISION: (Effort.MEDIUM, 8192, False),
    Role.AUDIO: (Effort.MEDIUM, 8192, False),
    Role.VIDEO: (Effort.MEDIUM, 8192, False),
    Role.WATCHER: (Effort.MEDIUM, 4096, True),
    Role.SIMULATOR: (Effort.LOW, 2048, True),
}


# Reasoning routing (feature 113): how hard the model thinks follows the job. Customer chat
# stays light so replies come fast; reading what a photo or bill says is medium; the watcher's
# note on a payment is high; reading a payment screenshot, where money is at stake, gets the
# deepest setting. A job not listed takes its role's setting; a call may still set its own.
JOB_ROUTES: dict[str, tuple[Effort, int]] = {
    # job (prompt name): (effort, max_tokens incl. reasoning)
    "order_agent": (Effort.LOW, 4096),
    "shop_assistant": (Effort.LOW, 4096),
    "owner_message": (Effort.LOW, 4096),
    "onboarding": (Effort.LOW, 4096),
    "handover_summary": (Effort.LOW, 2048),
    "customer_sim": (Effort.LOW, 2048),
    "post_caption": (Effort.LOW, 2048),
    "delivery_proof": (Effort.MEDIUM, 4096),
    "unboxing_check": (Effort.HIGH, 16384),
    "call_guardian": (Effort.MEDIUM, 4096),
    "business_coach": (Effort.LOW, 4096),
    "translate": (Effort.LOW, 2048),
    "translate_product": (Effort.LOW, 4096),
    "map_columns": (Effort.LOW, 2048),
    "map_product_columns": (Effort.LOW, 2048),
    "product_draft": (Effort.MEDIUM, 8192),
    "read_bill": (Effort.MEDIUM, 8192),
    "read_purchase": (Effort.MEDIUM, 8192),
    "post_product": (Effort.MEDIUM, 8192),
    "private_photo": (Effort.LOW, 1024),
    "packing_check": (Effort.MEDIUM, 8192),
    "shelf_count": (Effort.MEDIUM, 8192),
    "video_products": (Effort.MEDIUM, 16384),
    "payment_evidence": (Effort.HIGH, 4096),
    "payment_screenshot": (Effort.XHIGH, 16384),
}


def job_of(prompt_ref: str | None) -> str | None:
    """'order_agent@v7' -> 'order_agent'."""
    return prompt_ref.split("@")[0] if prompt_ref else None


def route(config: RoleConfig, prompt_ref: str | None) -> tuple[Effort, int]:
    """The effort and token budget for a call: its job's, else its role's. Media may need
    more room than the job's own budget, so the larger of the two is kept."""
    job = job_of(prompt_ref)
    if job is None or job not in JOB_ROUTES:
        return config.effort, config.max_tokens
    effort, max_tokens = JOB_ROUTES[job]
    return effort, max(max_tokens, config.max_tokens)


def _secret(value: object) -> str | None:
    getter = getattr(value, "get_secret_value", None)
    return getter() if getter else None


def spark_endpoints(settings: Settings) -> list[Endpoint]:
    endpoints: list[Endpoint] = []
    meta_key = _secret(settings.meta_model_api_key)
    meta_url = settings.meta_model_api_base_url
    if settings.meta_model_api_enabled and meta_key and meta_url:
        endpoints.append(
            Endpoint("meta", meta_url, meta_key, settings.meta_spark_model, *SPARK_OPEN_PRICE)
        )
    if key := _secret(settings.baseten_api_key):
        endpoints.append(
            Endpoint(
                "baseten",
                settings.baseten_base_url,
                key,
                settings.spark_model_baseten,
                *SPARK_OPEN_PRICE,
            )
        )
    if settings.modal_spark_base_url:
        endpoints.append(
            Endpoint(
                "modal",
                settings.modal_spark_base_url,
                _secret(settings.modal_spark_api_key),
                settings.spark_model_modal,
                Decimal(0),
                Decimal(0),
            )
        )
    if key := _secret(settings.fireworks_api_key):
        endpoints.append(
            Endpoint(
                "fireworks",
                settings.fireworks_base_url,
                key,
                settings.spark_model_fireworks,
                *SPARK_OPEN_PRICE,
            )
        )
    return endpoints


def glimmer_endpoints(settings: Settings) -> list[Endpoint]:
    endpoints: list[Endpoint] = []
    if settings.glimmer_base_url:
        endpoints.append(
            Endpoint(
                "local-glimmer",
                settings.glimmer_base_url,
                _secret(settings.glimmer_api_key),
                settings.glimmer_model,
                Decimal(0),
                Decimal(0),
            )
        )
    if settings.modal_glimmer_base_url:
        # The hosted Glimmer endpoint runs on Modal GPU time paid from credits, not per token.
        endpoints.append(
            Endpoint(
                "modal-glimmer",
                settings.modal_glimmer_base_url,
                _secret(settings.modal_glimmer_api_key),
                settings.glimmer_model,
                Decimal(0),
                Decimal(0),
            )
        )
    return endpoints


def build_roles(settings: Settings) -> dict[Role, RoleConfig]:
    spark = spark_endpoints(settings)
    glimmer = glimmer_endpoints(settings)
    local_glimmer = [endpoint for endpoint in glimmer if endpoint.name == "local-glimmer"]
    roles: dict[Role, RoleConfig] = {}
    for role, (effort, max_tokens, prefers_glimmer) in _ROLE_SETTINGS.items():
        if settings.ai_privacy_mode:
            # Privacy mode deliberately fails closed if a local endpoint is not configured.
            chain = local_glimmer
        else:
            chain = [*glimmer, *spark] if prefers_glimmer else spark
        roles[role] = RoleConfig(tuple(chain), effort, max_tokens)
    return roles


def cost_millipaisa(
    endpoint: Endpoint, input_tokens: int, output_tokens: int, usd_to_pkr: Decimal
) -> int:
    """Thousandths of a paisa: USD per million tokens -> PKR -> paisa -> millipaisa."""
    usd = (
        endpoint.usd_per_m_input * input_tokens + endpoint.usd_per_m_output * output_tokens
    ) / Decimal(1_000_000)
    return int((usd * usd_to_pkr * 100 * 1000).to_integral_value())
