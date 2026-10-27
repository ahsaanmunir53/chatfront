"""bKash and Nagad (Bangladesh), UPI bank alerts (India), eSewa and Khalti (Nepal).
All unverified: written from public descriptions, not from real messages."""

from chatfront.payments.sms.base import SmsParser, pattern

PARSERS = (
    SmsParser(
        id="bd.bkash",
        provider="bKash",
        country="BD",
        currency="BDT",
        senders=("bKash",),
        patterns=(
            pattern(
                r"received\s+tk\s*{amount}\s+from\s+(?P<payer>\d{11}).*?trxid\s+(?P<tid>[a-z0-9]{8,})"
            ),
        ),
    ),
    SmsParser(
        id="bd.nagad",
        provider="Nagad",
        country="BD",
        currency="BDT",
        senders=("Nagad", "NAGAD"),
        patterns=(
            pattern(
                r"money\s+received.*?amount:\s*tk\s*{amount}.*?sender:\s*(?P<payer>\d{11}).*?txnid:\s*(?P<tid>[a-z0-9]{6,})"
            ),
        ),
    ),
    SmsParser(
        id="in.upi",
        provider="UPI bank alerts",
        country="IN",
        currency="INR",
        senders=("HDFCBK", "SBIUPI", "ICICIB", "AXISBK", "KOTAKB", "PAYTMB"),
        patterns=(
            pattern(
                r"credited\s+(?:by|with)\s+(?:rs\.?|inr)\s*{amount}.*?from\s+(?P<payer>[\w.\-]+@\w+).*?upi\s*ref\s*no\.?\s*(?P<tid>\d{12})"
            ),
            pattern(
                r"(?:rs\.?|inr)\s*{amount}\s+credited\s+to\s+a/c.*?(?:upi\s*ref(?:\s*no)?|ref\s*no)[.:\s]*(?P<tid>\d{12})"
            ),
        ),
    ),
    SmsParser(
        id="np.esewa",
        provider="eSewa",
        country="NP",
        currency="NPR",
        senders=("eSewa",),
        patterns=(
            pattern(
                r"rs\.?\s*{amount}\s+received\s+from\s+(?P<payer>[^(]+?)\s*\(.*?transaction\s*(?:code|id)[:\s]*(?P<tid>[a-z0-9]{6,})"
            ),
        ),
    ),
    SmsParser(
        id="np.khalti",
        provider="Khalti",
        country="NP",
        currency="NPR",
        senders=("Khalti",),
        patterns=(
            pattern(
                r"received\s+rs\.?\s*{amount}\s+from\s+(?P<payer>[^(]+?)\s*\(.*?ref[:\s]*(?P<tid>[a-z0-9]{6,})"
            ),
        ),
    ),
)
