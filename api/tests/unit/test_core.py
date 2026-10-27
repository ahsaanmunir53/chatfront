import logging
import re
import uuid
from datetime import UTC, date, datetime

import pytest
from pydantic import SecretStr

from chatfront.core import clock, ids
from chatfront.core.errors import AppError
from chatfront.core.logging import RedactingFilter, redact
from chatfront.core.money import parse_amount, to_minor
from chatfront.core.phone import hash_phone, mask_phone, normalise_phone
from chatfront.core.secretbox import seal, unseal
from chatfront.core.settings import Settings


class TestMoney:
    def test_to_minor_units(self) -> None:
        assert to_minor(2500) == 250_000
        assert to_minor("2,497.50") == 249_750
        assert to_minor("0.005") == 1  # rounds half up to a whole paisa
        assert to_minor("1,00,000", digits=0) == 100_000

    def test_rejects_bad_and_negative(self) -> None:
        with pytest.raises(AppError):
            to_minor("abc")
        with pytest.raises(AppError):
            to_minor(-1)

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Rs. 2,500.00 received", 250_000),
            ("PKR 1,00,000 from Ali", 10_000_000),
            ("amount 499", 49_900),
            ("no money here", None),
        ],
    )
    def test_parse_amount(self, text: str, expected: int | None) -> None:
        assert parse_amount(text) == expected


class TestPhone:
    @pytest.mark.parametrize(
        "raw", ["0300-1234567", "+92 300 1234567", "923001234567", "0092 300 1234567"]
    )
    def test_normalise_pakistani_forms(self, raw: str) -> None:
        assert normalise_phone(raw) == "+923001234567"

    def test_rejects_short(self) -> None:
        with pytest.raises(AppError):
            normalise_phone("12345")

    def test_hash_is_keyed_and_stable(self) -> None:
        a = hash_phone("+923001234567", "k1")
        assert a == hash_phone("+923001234567", "k1")
        assert a != hash_phone("+923001234567", "k2")
        assert len(a) == 64

    def test_mask(self) -> None:
        assert mask_phone("+923001234567") == "+92 300 ***4567"
        assert mask_phone("+1") == "***"


class TestClock:
    def test_karachi_day_bounds(self) -> None:
        start, end = clock.local_day_bounds(date(2026, 10, 27), "Asia/Karachi")
        assert start == datetime(2026, 10, 26, 19, 0, tzinfo=UTC)
        assert end == datetime(2026, 10, 27, 19, 0, tzinfo=UTC)

    def test_naive_rejected(self) -> None:
        with pytest.raises(ValueError, match="naive"):
            clock.ensure_utc(datetime(2026, 1, 1))

    def test_local_today(self) -> None:
        late_utc = datetime(2026, 10, 26, 20, 0, tzinfo=UTC)
        assert clock.local_today("Asia/Karachi", late_utc) == date(2026, 10, 27)
        assert clock.utcnow().tzinfo is UTC


class TestIds:
    def test_uuid7_is_version_7_and_ordered(self) -> None:
        first, second = ids.new_id(), ids.new_id()
        assert first.version == 7
        assert isinstance(first, uuid.UUID)
        assert first.int >> 80 <= second.int >> 80

    def test_short_code(self) -> None:
        code = ids.short_code()
        assert len(code) == 6
        assert not set(code) & set("ILOU")


class TestRedaction:
    @pytest.mark.parametrize(
        "text",
        [
            "from +92 300 1234567 ok",
            "call 0300-1234567",
            "tid 12345678901 done",
            "mail ali@example.com",
            "Authorization: Bearer abc.def-123",
        ],
    )
    def test_redacts_pii(self, text: str) -> None:
        cleaned = redact(text)
        assert "[redacted]" in cleaned
        assert "1234567" not in cleaned
        assert "example.com" not in cleaned
        assert "abc.def" not in cleaned

    def test_keeps_dates_and_amounts(self) -> None:
        assert redact("on 2026-10-27 paid 250000") == "on 2026-10-27 paid 250000"

    def test_filter_rewrites_record(self) -> None:
        record = logging.LogRecord(
            "x", logging.INFO, __file__, 1, "phone %s", ("03001234567",), None
        )
        RedactingFilter().filter(record)
        assert record.getMessage() == "phone [redacted]"


def test_secretbox_round_trip() -> None:
    sealed = seal("token-123", "key")
    assert sealed != "token-123"
    assert unseal(sealed, "key") == "token-123"
    with pytest.raises(AppError):
        unseal(sealed, "other-key")


def test_production_refuses_dev_secrets() -> None:
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        Settings(env="prod").require_production_secrets()
    strong = SecretStr("x" * 40)
    Settings(
        env="prod", secret_key=strong, phone_hash_key=strong, cron_secret=strong
    ).require_production_secrets()


def test_database_url_gets_asyncpg_driver() -> None:
    assert Settings(database_url="postgres://u:p@h/db").database_url.startswith(
        "postgresql+asyncpg://"
    )


def test_env_example_documents_every_setting() -> None:
    from pathlib import Path

    example = Path(__file__).parents[3] / ".env.example"
    documented = set(re.findall(r"^([A-Z0-9_]+)=", example.read_text(), re.M))
    assert documented == {name.upper() for name in Settings.model_fields}


def test_empty_env_values_mean_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_RECORD_DIR", "")
    monkeypatch.setenv("WEB_DIST_DIR", "")
    settings = Settings()
    assert settings.ai_record_dir is None
    assert settings.web_dist_dir.name == "dist"
