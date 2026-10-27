"""Credit messages from wallets and banks, read by one parser per provider.

Every parser names the provider, country and currency, the sender ids it trusts and the
patterns of a credit message. A parser stays unverified until it passes on a real message
from that provider (tests/fixtures/sms/); the payment check never approves an order on an
unverified parser alone, and sends the case to the owner with Approve and Reject.
"""

from chatfront.payments.sms.base import Credit, SmsParser
from chatfront.payments.sms.registry import PARSERS, parse_sms, parsers_for

__all__ = ["PARSERS", "Credit", "SmsParser", "parse_sms", "parsers_for"]
