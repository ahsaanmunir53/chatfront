"""Delivery charge by city and weight, with a free-delivery threshold (feature 94).

The shop's own rates live in its profile (`delivery_rates`, minor units), set by the owner in
chat; until then the country pack's starting rates apply, cheaper inside the shop's own city.
The charge is worked out by code every time the cart changes; a model only passes on the city.
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from chatfront.catalog.service import DELIVERY_RATES
from chatfront.core.countries import CountryPack, get_pack
from chatfront.core.models import Tenant
from chatfront.templates import get_template


@dataclass(frozen=True, slots=True)
class Rates:
    cities: dict[str, int]  # canonical city name -> charge, minor units
    default: int | None  # anywhere else; None when the shop has not said
    per_kg: int
    included_kg: int
    free_over: int | None


@dataclass(frozen=True, slots=True)
class Quote:
    city: str
    fee: int | None  # None: the shop confirms the charge itself
    free: bool  # the free-delivery threshold was reached


def _fold(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def city_name(text: str, pack: CountryPack) -> str:
    """The pack's name for a city ("lhr", "لاہور" -> "Lahore"), else the words as given."""
    wanted = _fold(text)
    for city in pack.cities:
        if wanted in (_fold(city.name), *map(_fold, city.aliases)):
            return city.name
    return " ".join(text.split()).title() if text.isascii() else " ".join(text.split())


def rates_for(tenant: Tenant) -> Rates:
    pack = get_pack(tenant.country)
    scale = 10**pack.minor_digits
    profile: Mapping[str, Any] = tenant.profile or {}
    own: Mapping[str, Any] = profile.get(DELIVERY_RATES) or {}
    start = pack.delivery
    cities: dict[str, int] = {}
    home = profile.get("city")
    if start is not None and home:
        cities[city_name(str(home), pack)] = start.local_fee * scale
    cities.update({city_name(name, pack): int(fee) for name, fee in own.get("cities", {}).items()})

    def pick(key: str, fallback: int | None) -> int | None:
        return int(own[key]) if own.get(key) is not None else fallback

    return Rates(
        cities=cities,
        default=pick("default", start.national_fee * scale if start else None),
        per_kg=pick("per_kg", start.per_kg * scale if start else 0) or 0,
        included_kg=pick("included_kg", start.included_kg if start else 0) or 0,
        free_over=pick("free_over", start.free_over * scale if start and start.free_over else None),
    )


def quote(rates: Rates, city: str, weight_g: int, subtotal: int) -> Quote:
    if rates.free_over is not None and subtotal >= rates.free_over:
        return Quote(city, 0, free=True)
    base = rates.cities.get(city, rates.default)
    if base is None:
        return Quote(city, None, free=False)
    extra_kg = max(0, math.ceil(weight_g / 1000) - rates.included_kg)
    return Quote(city, base + extra_kg * rates.per_kg, free=False)


def weight_of(tenant: Tenant, attributes: Mapping[str, Any]) -> int:
    """Grams per piece: the product's own weight, else its template's default."""
    raw = str(attributes.get("weight_g", ""))
    return int(raw) if raw.isdigit() else get_template(tenant.template).default_weight_g
