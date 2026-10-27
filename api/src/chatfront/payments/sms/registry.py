"""Every parser, by id, and the lookup the SMS forwarder and payment check use."""

from __future__ import annotations

from chatfront.payments.sms.base import Credit, SmsParser
from chatfront.payments.sms.parsers import africa, americas_mena, asia_pacific, pakistan, south_asia

PARSERS: dict[str, SmsParser] = {
    parser.id: parser
    for module in (pakistan, south_asia, africa, asia_pacific, americas_mena)
    for parser in module.PARSERS
}


def parsers_for(country: str) -> list[SmsParser]:
    return [parser for parser in PARSERS.values() if parser.country == country.upper()]


def parse_sms(sender: str, body: str, country: str) -> Credit | None:
    """The credit in a message from a sender one of the country's parsers trusts."""
    for parser in parsers_for(country):
        if parser.trusts(sender) and (credit := parser.parse(body)):
            return credit
    return None
