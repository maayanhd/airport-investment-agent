import httpx
import pytest
import respx

from airport_agent.data.http import FetchError, HttpFetcher

URL = "https://example.test/data.csv"


def make_fetcher(sleeps: list[float]) -> HttpFetcher:
    return HttpFetcher(retries=2, backoff_seconds=0.5, sleep=sleeps.append)


@respx.mock
def test_returns_body_on_success() -> None:
    respx.get(URL).respond(200, text="a,b\n1,2\n")
    assert make_fetcher([]).get_text(URL) == "a,b\n1,2\n"


@respx.mock
def test_retries_then_succeeds_with_exponential_backoff() -> None:
    route = respx.get(URL).mock(
        side_effect=[httpx.Response(503), httpx.Response(503), httpx.Response(200, text="ok")]
    )
    sleeps: list[float] = []
    assert make_fetcher(sleeps).get_text(URL) == "ok"
    assert route.call_count == 3
    assert sleeps == [0.5, 1.0]


@respx.mock
def test_raises_fetch_error_after_exhausting_retries() -> None:
    respx.get(URL).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(FetchError):
        make_fetcher([]).get_text(URL)
