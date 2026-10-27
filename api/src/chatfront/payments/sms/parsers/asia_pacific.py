"""GCash and Maya (Philippines); GoPay, OVO and DANA (Indonesia), read from app
notifications. All unverified."""

from chatfront.payments.sms.base import SmsParser, pattern

PARSERS = (
    SmsParser(
        id="ph.gcash",
        provider="GCash",
        country="PH",
        currency="PHP",
        senders=("GCash",),
        patterns=(
            pattern(
                r"received\s+php\s*{amount}\s+of\s+gcash\s+from\s+(?P<payer>[^\d]+?)\s*\d{0,11}\.?\s*ref\.?\s*no\.?\s*(?P<tid>\d{10,})"
            ),
        ),
    ),
    SmsParser(
        id="ph.maya",
        provider="Maya",
        country="PH",
        currency="PHP",
        senders=("Maya",),
        patterns=(
            pattern(
                r"received\s+(?:php|₱)\s*{amount}\s+from\s+(?P<payer>.+?)\.\s*ref\s*no\.?\s*(?P<tid>\d{8,})"
            ),
        ),
    ),
    SmsParser(
        id="id.gopay",
        provider="GoPay",
        country="ID",
        currency="IDR",
        senders=("GoPay", "Gojek"),
        decimal=",",
        source="notification",
        patterns=(
            pattern(
                r"terima\s+rp\s*{amount}\s+dari\s+(?P<payer>.+?)\.\s*id\s+transaksi[:\s]*(?P<tid>[a-z0-9-]{6,})"
            ),
        ),
    ),
    SmsParser(
        id="id.ovo",
        provider="OVO",
        country="ID",
        currency="IDR",
        senders=("OVO",),
        decimal=",",
        source="notification",
        patterns=(
            pattern(
                r"menerima\s+rp\s*{amount}\s+dari\s+(?P<payer>\S+)\.\s*no\.?\s*ref[:\s]*(?P<tid>[a-z0-9]{6,})"
            ),
        ),
    ),
    SmsParser(
        id="id.dana",
        provider="DANA",
        country="ID",
        currency="IDR",
        senders=("DANA",),
        decimal=",",
        source="notification",
        patterns=(
            pattern(
                r"menerima\s+saldo\s+rp\s*{amount}\s+dari\s+(?P<payer>.+?)\.\s*id\s+transaksi[:\s]*(?P<tid>\d{8,})"
            ),
        ),
    ),
)
