import pytest
from sqlalchemy import select

from chatfront.audit.models import AuditEntry
from chatfront.channels.connect import connect
from chatfront.channels.models import Channel, ChannelAccount
from chatfront.core.db import Database
from chatfront.core.errors import AppError
from chatfront.core.secretbox import unseal
from chatfront.core.settings import get_settings
from chatfront.modules.registry import core_modules
from chatfront.seed.__main__ import seed


async def test_connect_a_whatsapp_number_then_update_it(clean_db: Database) -> None:
    await seed(clean_db, core_modules())
    settings = get_settings()
    account = await connect(
        clean_db,
        settings,
        shop="gulmohar-lawn",
        channel=Channel.WHATSAPP,
        account_id="PN9",
        handle="0300-1234567",
        token="real-token",
    )
    assert (account.public_handle, account.display) == ("923001234567", "+92 300 ***4567")
    assert account.access_token_sealed is not None
    assert (
        unseal(account.access_token_sealed, settings.secret_key.get_secret_value()) == "real-token"
    )
    again = await connect(
        clean_db,
        settings,
        shop="gulmohar-lawn",
        channel=Channel.WHATSAPP,
        account_id="PN9",
        handle="03001234568",
        token=None,
    )
    assert again.id == account.id
    assert again.access_token_sealed == account.access_token_sealed  # kept when not given
    async with clean_db.system() as session:
        actions = list(
            await session.scalars(
                select(AuditEntry.action).where(AuditEntry.action == "channel.connected")
            )
        )
        accounts = list(
            await session.scalars(
                select(ChannelAccount).where(ChannelAccount.channel == Channel.WHATSAPP)
            )
        )
    assert len(actions) == 2
    assert len(accounts) == 1


async def test_connect_refuses_bad_requests(clean_db: Database) -> None:
    await seed(clean_db, core_modules())
    settings = get_settings()
    await connect(
        clean_db,
        settings,
        shop="noor-salon",
        channel=Channel.INSTAGRAM,
        account_id="IG5",
        handle="@noor.salon",
        token=None,
    )
    with pytest.raises(AppError, match="another shop"):
        await connect(
            clean_db,
            settings,
            shop="gulmohar-lawn",
            channel=Channel.INSTAGRAM,
            account_id="IG5",
            handle="x",
            token=None,
        )
    with pytest.raises(AppError, match="not found"):
        await connect(
            clean_db,
            settings,
            shop="nope",
            channel=Channel.MESSENGER,
            account_id="P1",
            handle="x",
            token=None,
        )
    with pytest.raises(AppError, match="web chat"):
        await connect(
            clean_db,
            settings,
            shop="noor-salon",
            channel=Channel.WEBCHAT,
            account_id="w",
            handle="x",
            token=None,
        )


async def test_numbers_in_the_settings_are_connected_once_at_start(clean_db: Database) -> None:
    from chatfront.channels.connect import connect_from_settings, planned

    await seed(clean_db, core_modules())
    settings = get_settings().model_copy(
        update={"whatsapp_connect": "gulmohar-lawn:1187238694481159:+1 (555) 158-0703"}
    )
    assert await connect_from_settings(clean_db, settings) == ["gulmohar-lawn"]
    assert await connect_from_settings(clean_db, settings) == []  # already connected
    async with clean_db.system() as session:
        account = await session.scalar(
            select(ChannelAccount).where(ChannelAccount.external_id == "1187238694481159")
        )
    assert account is not None
    assert (account.channel, account.public_handle) == (Channel.WHATSAPP, "15551580703")
    assert planned("") == []
    with pytest.raises(ValueError, match="shop:phone_number_id:number"):
        planned("gulmohar-lawn:123")
