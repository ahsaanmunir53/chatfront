"""M-Pesa (Kenya), MTN MoMo (Ghana), OPay and PalmPay (Nigeria). All unverified."""

from chatfront.payments.sms.base import SmsParser, pattern

PARSERS = (
    SmsParser(
        id="ke.mpesa",
        provider="M-Pesa",
        country="KE",
        currency="KES",
        senders=("MPESA", "M-PESA"),
        patterns=(
            pattern(
                r"^(?P<tid>[a-z0-9]{10})\s+confirmed\.?\s+you\s+have\s+received\s+ksh\s*{amount}\s+from\s+(?P<payer>.+?)\s+(?:\d{9,12}\s+)?on\b"
            ),
        ),
    ),
    SmsParser(
        id="gh.mtn_momo",
        provider="MTN MoMo",
        country="GH",
        currency="GHS",
        senders=("MobileMoney", "MTN MoMo"),
        patterns=(
            pattern(
                r"payment\s+received\s+for\s+ghs\s*{amount}\s+from\s+(?P<payer>.+?)\s+current\s+balance.*?transaction\s+id[:\s]*(?P<tid>\d{8,})"
            ),
        ),
    ),
    SmsParser(
        id="ng.opay",
        provider="OPay",
        country="NG",
        currency="NGN",
        senders=("OPay",),
        patterns=(
            pattern(
                r"credit\s+alert[:\s]+(?:ngn|₦)\s*{amount}\s+from\s+(?P<payer>.+?)\s+has\s+been\s+received.*?transaction\s+id[:\s]*(?P<tid>\d{10,})"
            ),
        ),
    ),
    SmsParser(
        id="ng.palmpay",
        provider="PalmPay",
        country="NG",
        currency="NGN",
        senders=("PalmPay",),
        patterns=(
            pattern(
                r"you\s+received\s+(?:ngn|₦)\s*{amount}\s+from\s+(?P<payer>.+?)\.\s*ref[:\s]*(?P<tid>[a-z0-9]{8,})"
            ),
        ),
    ),
)
