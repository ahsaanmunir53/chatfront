import httpx
import pytest
import respx

from chatfront.core.http import UpstreamError, request


@respx.mock
async def test_retries_retryable_status_then_succeeds() -> None:
    route = respx.get("https://x.test/a").mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json={"ok": True})]
    )
    async with httpx.AsyncClient() as client:
        response = await request(client, "GET", "https://x.test/a", service="x", backoff_seconds=0)
    assert response.json() == {"ok": True}
    assert route.call_count == 2


@respx.mock
async def test_does_not_retry_client_errors() -> None:
    route = respx.post("https://x.test/a").mock(return_value=httpx.Response(400, text="bad"))
    async with httpx.AsyncClient() as client:
        with pytest.raises(UpstreamError) as caught:
            await request(client, "POST", "https://x.test/a", service="x", backoff_seconds=0)
    assert route.call_count == 1
    assert caught.value.status == 400
    assert not caught.value.retryable
    assert "HTTP 400" in caught.value.message
    assert "bad" not in caught.value.message


@respx.mock
async def test_gives_up_after_retries_on_timeouts() -> None:
    route = respx.get("https://x.test/a").mock(side_effect=httpx.ConnectTimeout("slow"))
    async with httpx.AsyncClient() as client:
        with pytest.raises(UpstreamError) as caught:
            await request(
                client, "GET", "https://x.test/a", service="x", retries=1, backoff_seconds=0
            )
    assert route.call_count == 2
    assert caught.value.retryable
    assert caught.value.status is None


@respx.mock
async def test_upstream_error_body_is_not_exposed_or_logged(caplog: pytest.LogCaptureFixture) -> None:
    secret = "Sensitive customer message: pay at +923001234567"
    respx.get("https://x.test/private").mock(return_value=httpx.Response(500, text=secret))
    async with httpx.AsyncClient() as client:
        with pytest.raises(UpstreamError) as caught:
            await request(
                client, "GET", "https://x.test/private", service="x", backoff_seconds=0
            )
    assert secret not in caught.value.message
    assert "+923001234567" not in caught.value.message
    assert secret not in caplog.text
    assert "HTTP 500" in caught.value.message
