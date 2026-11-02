"""Connect a WhatsApp number, Instagram account or Facebook Page to a shop.

    python -m chatfront.channels.connect --shop gulmohar-lawn --channel whatsapp \\
        --account-id <phone number id> --handle 923001234567 --token-env WA_TOKEN

The token is read from the environment variable named by --token-env, never typed on the
command line, and stored sealed. Without --token-env the platform token from settings is used.
"""

from __future__ import annotations

import argparse
import asyncio
import os

from sqlalchemy import select

from chatfront.audit.service import SYSTEM, record
from chatfront.channels.models import Channel, ChannelAccount
from chatfront.core.db import Database
from chatfront.core.errors import AppError, NotFound
from chatfront.core.models import Tenant
from chatfront.core.phone import mask_phone, normalise_phone
from chatfront.core.secretbox import seal
from chatfront.core.settings import Settings, get_settings


async def connect(
    db: Database,
    settings: Settings,
    *,
    shop: str,
    channel: Channel,
    account_id: str,
    handle: str,
    token: str | None,
) -> ChannelAccount:
    """Add or update the account; one account id belongs to one shop only."""
    if channel is Channel.WEBCHAT:
        raise AppError("not_connectable", "The web chat is created with the shop")
    display = handle
    if channel is Channel.WHATSAPP:
        number = normalise_phone(handle)
        handle, display = number.lstrip("+"), mask_phone(number)
    sealed = seal(token, settings.secret_key.get_secret_value()) if token else None
    async with db.system() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.slug == shop))
        if tenant is None:
            raise NotFound("Shop")
        account = await session.scalar(
            select(ChannelAccount).where(
                ChannelAccount.channel == channel, ChannelAccount.external_id == account_id
            )
        )
        if account is not None and account.tenant_id != tenant.id:
            raise AppError(
                "account_taken", "This account is connected to another shop", status_code=409
            )
        if account is None:
            account = ChannelAccount(tenant_id=tenant.id, channel=channel, external_id=account_id)
            session.add(account)
        account.public_handle, account.display, account.active = handle, display, True
        if sealed:
            account.access_token_sealed = sealed
        await session.flush()
        await record(
            session,
            tenant.id,
            SYSTEM,
            "channel.connected",
            entity=("channel_account", str(account.id)),
            data={"channel": channel, "display": display},
        )
    return account


CONNECTABLE = {
    "whatsapp": Channel.WHATSAPP,
    "instagram": Channel.INSTAGRAM,
    "messenger": Channel.MESSENGER,
}


def planned(value: str, default: Channel | None = None) -> list[tuple[Channel, str, str, str]]:
    """(channel, shop, account id, handle) for each entry: "channel:shop:id:handle", or
    "shop:id:handle" where a default channel is given (WHATSAPP_CONNECT)."""
    found = []
    for entry in value.split(";"):
        parts = [part.strip() for part in entry.split(":")]
        if default is not None and len(parts) == 3 and all(parts):
            found.append((default, parts[0], parts[1], parts[2]))
        elif len(parts) == 4 and all(parts) and parts[0].lower() in CONNECTABLE:
            found.append((CONNECTABLE[parts[0].lower()], parts[1], parts[2], parts[3]))
        elif entry.strip():
            shape = "shop:id:handle" if default else "channel:shop:id:handle"
            raise ValueError(f"connect entry is not {shape}: {entry}")
    return found


async def connect_from_settings(db: Database, settings: Settings) -> list[str]:
    """Connect the accounts in WHATSAPP_CONNECT and CONNECT_ACCOUNTS at start; one already
    connected is left as it is."""
    done = []
    wanted = [
        *planned(settings.whatsapp_connect, Channel.WHATSAPP),
        *planned(settings.connect_accounts),
    ]
    for channel, shop, account_id, handle in wanted:
        async with db.system() as session:
            known = await session.scalar(
                select(ChannelAccount.id)
                .join(Tenant, Tenant.id == ChannelAccount.tenant_id)
                .where(
                    ChannelAccount.channel == channel,
                    ChannelAccount.external_id == account_id,
                    ChannelAccount.active.is_(True),
                    Tenant.slug == shop,
                )
            )
        if known is None:
            await connect(
                db,
                settings,
                shop=shop,
                channel=channel,
                account_id=account_id,
                handle=handle,
                token=None,
            )
            done.append(f"{shop} ({channel})")
    return done


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--shop", required=True, help="the shop's slug, e.g. gulmohar-lawn")
    parser.add_argument("--channel", required=True, choices=["whatsapp", "instagram", "messenger"])
    parser.add_argument(
        "--account-id",
        required=True,
        help="WhatsApp phone number id, Instagram account id, or Facebook Page id",
    )
    parser.add_argument(
        "--handle", required=True, help="the public number, Instagram username, or Page username"
    )
    parser.add_argument("--token-env", help="name of the env var that holds this account's token")
    args = parser.parse_args()
    token = os.environ.get(args.token_env) if args.token_env else None
    if args.token_env and not token:
        parser.error(f"{args.token_env} is not set")
    settings = get_settings()
    db = Database(settings.database_url, pool_size=1)

    async def run() -> ChannelAccount:
        try:
            return await connect(
                db,
                settings,
                shop=args.shop,
                channel=Channel(args.channel),
                account_id=args.account_id,
                handle=args.handle,
                token=token,
            )
        finally:
            await db.dispose()

    account = asyncio.run(run())
    print(f"connected {account.channel} {account.display} to {args.shop}")


if __name__ == "__main__":
    main()
