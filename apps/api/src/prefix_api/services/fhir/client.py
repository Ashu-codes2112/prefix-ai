"""A small FHIR R4 REST client: incremental search, paging and retries.

``search(type, since)`` asks for resources updated after ``since`` (``_lastUpdated=gt...``),
oldest first, and follows the Bundle's ``next`` links. Network errors, 429 and 5xx are retried
with exponential backoff (honouring ``Retry-After``); anything else fails the sync with the
status code only, never a response body, which could hold patient data.
"""

import time
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import urljoin

import httpx2

type Sleep = Callable[[float], None]
type Resource = dict[str, Any]

PAGE_SIZE = 100
MAX_PAGES = 200
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


class FhirError(Exception):
    """The server could not be read. The message is safe to store and show to admins."""


class FhirClient:
    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        client: httpx2.Client | None = None,
        retries: int = 3,
        backoff_seconds: float = 1.0,
        sleep: Sleep = time.sleep,
    ) -> None:
        self._base = base_url.rstrip("/") + "/"
        self._client = client or httpx2.Client(timeout=20.0)
        self._headers = {"Accept": "application/fhir+json"}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"
        self._retries = retries
        self._backoff = backoff_seconds
        self._sleep = sleep

    def search(
        self, resource_type: str, since: str | None, extra: dict[str, str] | None = None
    ) -> Iterator[Resource]:
        params: dict[str, str] = {
            "_count": str(PAGE_SIZE),
            "_sort": "_lastUpdated",
            **(extra or {}),
        }
        if since:
            params["_lastUpdated"] = f"gt{since}"
        url: str | None = urljoin(self._base, resource_type)
        query: dict[str, str] | None = params
        for _ in range(MAX_PAGES):
            if url is None:
                return
            bundle = self._get(url, query)
            if bundle.get("resourceType") != "Bundle":
                raise FhirError(f"{resource_type} search did not return a Bundle")
            for entry in bundle.get("entry", []):
                resource = entry.get("resource") if isinstance(entry, dict) else None
                if isinstance(resource, dict) and resource.get("resourceType") == resource_type:
                    yield resource
            url = next(
                (
                    link.get("url")
                    for link in bundle.get("link", [])
                    if isinstance(link, dict) and link.get("relation") == "next"
                ),
                None,
            )
            query = None  # the next link carries its own query
        raise FhirError(f"{resource_type} search has more than {MAX_PAGES} pages")

    def read(self, resource_type: str, resource_id: str) -> Resource:
        resource = self._get(urljoin(self._base, f"{resource_type}/{resource_id}"), None)
        if resource.get("resourceType") != resource_type:
            raise FhirError(f"{resource_type} read returned something else")
        return resource

    def _get(self, url: str, params: dict[str, str] | None) -> Resource:
        last = "no response"
        for attempt in range(self._retries + 1):
            try:
                response = self._client.get(url, params=params, headers=self._headers)
            except httpx2.HTTPError as exc:
                last = type(exc).__name__
            else:
                if response.status_code == 200:
                    try:
                        body = response.json()
                    except ValueError as exc:
                        raise FhirError("The FHIR server sent something that is not JSON") from exc
                    if not isinstance(body, dict):
                        raise FhirError("The FHIR server sent something that is not a resource")
                    return body
                last = f"HTTP {response.status_code}"
                if response.status_code not in _RETRY_STATUS:
                    raise FhirError(f"The FHIR server answered {last}")
                retry_after = response.headers.get("Retry-After", "")
                if attempt < self._retries and retry_after.isdigit():
                    self._sleep(min(float(retry_after), 60.0))
                    continue
            if attempt < self._retries:
                self._sleep(self._backoff * (2**attempt))
        raise FhirError(f"The FHIR server could not be reached ({last}) after retries")
