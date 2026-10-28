"""Rules behind posting and sales, without a database: what counts as a claim in a live sale
(feature 8), where a lead came from (78, 81), which model captions are kept (7), and the
pictures code draws (3)."""

from __future__ import annotations

import io
import uuid
from datetime import timedelta

import pytest
from PIL import Image

from chatfront.catalog import sales
from chatfront.catalog.models import FlashSale, Product
from chatfront.channels.leads import LeadKind, source_of
from chatfront.core.clock import utcnow
from chatfront.core.errors import AppError
from chatfront.core.models import Tenant
from chatfront.media.posters import SIZES, Card, Shape, TemplateImages, drawable
from chatfront.orders import live_claims
from chatfront.publish import captions


class TestClaims:
    @pytest.mark.parametrize(
        ("text", "number"),
        [("2", 2), ("#2", 2), ("L2", 2), ("no. 12", 12), ("2 chahiye", 2), (" 7 mujhe!", 7)],
    )
    def test_numbers_that_claim(self, text: str, number: int) -> None:
        assert sales.claim_number(text) == number

    @pytest.mark.parametrize("text", [None, "", "2 suit chahiye kal", "price 2500?", "123"])
    def test_other_words_do_not(self, text: str | None) -> None:
        assert sales.claim_number(text) is None

    def test_a_colour_button(self) -> None:
        variant = uuid.uuid4()
        assert live_claims.parse_button(f"sv:3:{variant}") == (3, variant)
        assert live_claims.parse_button("sv:x:y") is None
        assert live_claims.parse_button("pl:1") is None

    def test_a_sale_is_on_between_its_times_until_stopped(self) -> None:
        now = utcnow()
        sale = FlashSale(starts_at=now - timedelta(minutes=1), ends_at=now + timedelta(hours=1))
        assert sales.is_on(sale, now)
        assert not sales.is_on(sale, now + timedelta(hours=2))
        sale.stopped_at = now
        assert not sales.is_on(sale, now)

    def test_limits(self) -> None:
        now = utcnow()
        with pytest.raises(AppError):
            sales.check_window(now, now - timedelta(minutes=1))
        with pytest.raises(AppError):
            sales.check_window(now, now + timedelta(hours=sales.MAX_HOURS + 1))
        with pytest.raises(AppError):
            sales.check_items(0)
        sales.check_items(sales.MAX_ITEMS)


class TestLeads:
    def test_a_whatsapp_ad(self) -> None:
        found = source_of(
            {"referral": {"source_type": "ad", "source_id": "123", "headline": "Lawn sale"}}
        )
        assert found is not None
        assert (found.kind, found.ref, found.title) == (LeadKind.AD, "123", "Lawn sale")

    def test_an_instagram_ad_and_a_link(self) -> None:
        ad = source_of({"referral": {"source": "ADS", "ad_id": "9", "ref": "x"}})
        link = source_of({"referral": {"source": "SHORTLINK", "ref": "bio"}})
        assert ad is not None
        assert link is not None
        assert (ad.kind, ad.ref) == (LeadKind.AD, "9")
        assert (link.kind, link.ref) == (LeadKind.LINK, "bio")

    def test_stories(self) -> None:
        reply = source_of({"reply_to": {"story": {"id": "S", "url": "u"}}})
        mention = source_of({"attachment_type": "story_mention", "url": "m"})
        assert reply is not None
        assert mention is not None
        assert (reply.kind, reply.ref, reply.url) == (LeadKind.STORY_REPLY, "S", "u")
        assert (mention.kind, mention.url) == (LeadKind.STORY_MENTION, "m")

    def test_an_ordinary_message_is_no_lead(self) -> None:
        assert source_of({}) is None
        assert source_of({"reply_to": "mid.1", "referral": "nope"}) is None


class TestCaptions:
    def test_a_model_caption_with_a_number_is_not_used(self) -> None:
        assert captions.usable("Naya maal aa gaya!") == "Naya maal aa gaya!"
        assert captions.usable("Sirf 2500 mein!") is None
        assert captions.usable("صرف ۲۵۰۰ میں") is None  # Urdu digits too
        assert captions.usable("   ") is None

    def test_code_writes_the_prices_and_the_link(self) -> None:
        tenant = Tenant(name="Gulmohar", country="PK", currency="PKR", slug="g")
        product = Product(name="Lawn suit", price=485_000)
        text = captions.full_text(
            tenant, {"ur": "نیا مال", "en": "New stock"}, [product], "https://x/s/g"
        )
        assert text == "نیا مال\n\nNew stock\n\nLawn suit: Rs 4,850\n\nhttps://x/s/g"


class TestPictures:
    def photo(self) -> bytes:
        out = io.BytesIO()
        Image.new("RGB", (900, 600), (200, 170, 120)).save(out, "JPEG")
        return out.getvalue()

    @pytest.mark.parametrize("shape", list(Shape))
    def test_each_card_has_its_size(self, shape: Shape) -> None:
        card = Card("Gulmohar", "Lawn suit", "Rs 4,850", "clothing", "New")
        made = Image.open(io.BytesIO(TemplateImages().card(self.photo(), card, shape)))
        assert made.size == SIZES[shape]
        assert made.format == "JPEG"

    def test_a_cleaned_photo_is_square_and_balanced(self) -> None:
        cleaned = Image.open(io.BytesIO(TemplateImages().clean(self.photo())))
        assert cleaned.size == (1080, 1080)
        red, green, blue = cleaned.convert("RGB").getpixel((540, 540))
        assert max(red, green, blue) - min(red, green, blue) < 40  # the yellow cast is gone

    def test_latin_text_can_always_be_drawn(self) -> None:
        assert drawable("Lawn suit, Rs 4,850")
