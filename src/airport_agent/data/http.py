"""The single place that performs network I/O: timeout, retries, User-Agent."""

import time
from collections.abc import Callable, Mapping

import httpx

USER_AGENT = "airport-investment-agent/0.1 (+https://github.com/maayanhd/airport-investment-agent)"


class FetchError(Exception):
    """A source could not be fetched after all retries."""


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
        last_error: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                response = self._client.get(url, params=params)
                response.raise_for_status()
            except httpx.HTTPError as error:
                last_error = error
                if attempt < self._retries:
                    self._sleep(self._backoff * 2**attempt)
            else:
                return response.text
        raise FetchError(f"GET {url} failed after {self._retries + 1} attempts: {last_error}")
