"""The single place that performs network I/O: timeout, retries, User-Agent."""

import time
from collections.abc import Callable, Mapping

import httpx

USER_AGENT = "airport-investment-agent/0.1 (+https://github.com/maayanhd/airport-investment-agent)"
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR = 500


class FetchError(Exception):
    """A source could not be fetched."""


def _is_retryable(error: httpx.HTTPError) -> bool:
    """Retry network trouble, server errors and rate limits. Never retry a 404 or 403."""
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        return status >= HTTP_SERVER_ERROR or status == HTTP_TOO_MANY_REQUESTS

    return isinstance(error, httpx.TransportError)


class HttpFetcher:
    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        retries: int = 2,
        backoff_seconds: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client or httpx.Client(
            timeout=30.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        )

        self._retries = retries
        self._backoff = backoff_seconds
        self._sleep = sleep

    def get_text(self, url: str, params: Mapping[str, str] | None = None) -> str:
        attempts = 0

        while True:
            attempts += 1

            try:
                response = self._client.get(url, params=params)
                response.raise_for_status()
            except httpx.HTTPError as error:
                if not _is_retryable(error) or attempts > self._retries:
                    raise FetchError(
                        f"GET {url} failed after {attempts} attempt(s): {error}"
                    ) from error

                self._sleep(self._backoff * 2 ** (attempts - 1))
            else:
                return response.text
