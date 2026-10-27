"""Pix (Brazil), InstaPay and Vodafone Cash (Egypt), STC Pay (Saudi Arabia). All unverified."""

from chatfront.payments.sms.base import SmsParser, pattern

PARSERS = (
    SmsParser(
        id="br.pix",
        provider="Pix",
        country="BR",
        currency="BRL",
        senders=("Pix",),
        decimal=",",
        source="notification",
        patterns=(
            pattern(
                r"pix\s+recebido[:\s]+r\$\s*{amount}\s+de\s+(?P<payer>.+?)\.\s*id[:\s]*(?P<tid>e[a-z0-9]{20,})"
            ),
        ),
    ),
    SmsParser(
        id="eg.instapay",
        provider="InstaPay",
        country="EG",
        currency="EGP",
        senders=("InstaPay", "IPN"),
        patterns=(
            pattern(
                r"received\s+egp\s*{amount}\s+from\s+(?P<payer>.+?)\s+via\s+instapay.*?ref[:\s]*(?P<tid>\d{6,})"
            ),
            pattern(
                r"استلام\s+{amount}\s+جنيه\s+من\s+(?P<payer>.+?)\s+عبر\s+instapay.*?المرجع\s+(?P<tid>\d{6,})"
            ),
        ),
    ),
    SmsParser(
        id="eg.vodafone_cash",
        provider="Vodafone Cash",
        country="EG",
        currency="EGP",
        senders=("VF-Cash", "Vodafone"),
        patterns=(
            pattern(
                r"استلام\s+مبلغ\s+{amount}\s+جنيه\s+من\s+(?:رقم\s+)?(?P<payer>\d{11}).*?رقم\s+العملية\s+(?P<tid>\d{6,})"
            ),
        ),
    ),
    SmsParser(
        id="sa.stc_pay",
        provider="STC Pay",
        country="SA",
        currency="SAR",
        senders=("STC Pay", "stcpay"),
        patterns=(
            pattern(
                r"you\s+received\s+sar\s*{amount}\s+from\s+(?P<payer>.+?)\.\s*ref[:\s]*(?P<tid>\d{6,})"
            ),
            pattern(
                r"استلمت\s+{amount}\s+ريال\s+من\s+(?P<payer>.+?)\.\s*المرجع[:\s]*(?P<tid>\d{6,})"
            ),
        ),
    ),
)
