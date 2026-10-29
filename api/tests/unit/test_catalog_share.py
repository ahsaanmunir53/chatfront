"""Security regression tests for server-rendered product share pages."""

from chatfront.catalog.models import Product
from chatfront.catalog.share import page
from chatfront.core.models import Tenant


def test_share_page_escapes_product_shop_and_description_markup() -> None:
    tenant = Tenant(
        name='Shop "><img onerror="alert(1)">',
        default_lang="en",
        country="PK",
        currency="PKR",
    )
    product = Product(
        name='<script>alert("xss")</script>',
        description='"><img onerror="alert(1)">',
        price=485000,
    )

    rendered = page(
        tenant,
        product,
        share_url="https://shop.example/s/demo/p/ABC123",
        card_url="https://shop.example/cards/ABC123.png",
        shop_url='https://shop.example/s/demo?next="><script>alert(1)</script>',
    )

    assert '<script>alert("xss")</script>' not in rendered
    assert "<img onerror" not in rendered.lower()
    assert "&lt;script&gt;" in rendered
    assert "&lt;img onerror=" in rendered
    assert 'next=&quot;&gt;&lt;script&gt;' in rendered
    assert r"\\u003cscript\\u003e" in rendered
