"""JazzCash, Easypaisa and Pakistani bank credit alerts (IBFT and Raast). Unverified until
Ahsaan's real samples arrive."""

from chatfront.payments.sms.base import SmsParser, pattern

TID = r"(?:tid|trx\.?\s*id|transaction\s*id|txn\s*id)[\s:#.]*(?P<tid>\d{6,})"
BANK_REF = r"(?:txid|trx\s*id|ref|stan|rrn)[\s:#]*(?P<tid>[a-z0-9]{6,})"

PARSERS = (
    SmsParser(
        id="pk.jazzcash",
        provider="JazzCash",
        country="PK",
        currency="PKR",
        senders=("JazzCash", "8558"),
        patterns=(
            pattern(
                r"received\s+(?:rs\.?|pkr)\s*{amount}\s+from\s+(?P<payer>[^\d.]+)\s*[\d*]{0,13}[.,]?\s.*?"
                + TID
            ),
        ),
    ),
    SmsParser(
        id="pk.easypaisa",
        provider="Easypaisa",
        country="PK",
        currency="PKR",
        senders=("Easypaisa", "3737"),
        patterns=(
            pattern(
                r"(?:rs\.?|pkr)\s*{amount}\s+(?:has\s+been\s+)?received\s+from\s+(?P<payer>[^\d]+?)\s*[\d*]{0,13}\s+(?:in|to)\s.*?"
                + TID
            ),
        ),
    ),
    SmsParser(
        id="pk.bank",
        provider="Pakistani banks (IBFT, Raast)",
        country="PK",
        currency="PKR",
        senders=("HBL", "UBL", "MCB", "ABL", "BAFL", "MEEZAN", "Alfalah", "BankIslami", "8888"),
        patterns=(
            pattern(
                r"(?:pkr|rs\.?)\s*{amount}\s+received\s+via\s+raast\s+"
                r"from\s+(?P<payer>[a-z .]+?)\s*[(.].*?" + BANK_REF
            ),
            pattern(
                r"credited\s+(?:with|by)\s+(?:pkr|rs\.?)\s*{amount}\s+(?:via\s+\w+\s+)?"
                r"from\s+(?P<payer>[a-z .]+?)\.\s.*?" + BANK_REF
            ),
        ),
    ),
)
