"""An in-memory FHIR R4 server over synthetic fixtures, for tests and local demos.

It answers the searches the sync makes (``_lastUpdated=gt``, ``code``, ``_count`` with
``next`` links) and plain reads, and can be told to fail the next requests (for example
``[503, 429]``) to exercise retries. ``scripts/fhir_mock_server.py`` serves it over HTTP.

Fixture times are written as offsets: ``@-3d`` is three days ago and ``@+6d+45m`` is six days
ahead plus 45 minutes, both from 10:00 India time (04:30 UTC). So bookings are always in
the future, whenever the fixtures are loaded.
"""

import copy
import datetime as dt
import json
import re
from importlib import resources as package_files
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2

type Resource = dict[str, Any]

_OFFSET = re.compile(r"^@([+-]\d+)d(?:([+-]\d+)m)?$")
DEFAULT_PAGE = 100


def _resolve(value: Any, base: dt.datetime) -> Any:
    if isinstance(value, dict):
        return {k: _resolve(v, base) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, base) for v in value]
    if isinstance(value, str):
        match = _OFFSET.match(value)
        if match:
            days, minutes = int(match.group(1)), int(match.group(2) or 0)
            return (base + dt.timedelta(days=days, minutes=minutes)).isoformat()
    return value


def load_fixtures(now: dt.datetime) -> list[Resource]:
    raw = (
        package_files.files("prefix_api.fhir_fixtures").joinpath("fixtures.json").read_text("utf-8")
    )
    base = now.astimezone(dt.UTC).replace(hour=4, minute=30, second=0, microsecond=0)
    return [_resolve(r, base) for r in json.loads(raw)["resources"]]


def _updated(resource: Resource) -> dt.datetime:
    return dt.datetime.fromisoformat(resource["meta"]["lastUpdated"])


class FixtureFhirServer:
    def __init__(
        self, resources: list[Resource] | None = None, *, now: dt.datetime | None = None
    ) -> None:
        self.now = now or dt.datetime.now(dt.UTC)
        self._resources = resources if resources is not None else load_fixtures(self.now)
        self.fail_next: list[int] = []
        self.requests: list[str] = []

    # Changing the data, as the EHR would --------------------------------------------------------

    def upsert(self, resource: Resource, *, at: dt.datetime) -> None:
        resource = copy.deepcopy(resource)
        resource.setdefault("meta", {})["lastUpdated"] = at.isoformat()
        self._resources = [
            r
            for r in self._resources
            if not (r["resourceType"] == resource["resourceType"] and r["id"] == resource["id"])
        ] + [resource]

    def get(self, resource_type: str, resource_id: str) -> Resource:
        return copy.deepcopy(
            next(
                r
                for r in self._resources
                if r["resourceType"] == resource_type and r["id"] == resource_id
            )
        )

    # HTTP ---------------------------------------------------------------------------------------

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._handle_request)

    def _handle_request(self, request: httpx2.Request) -> httpx2.Response:
        status, body = self.handle(str(request.url))
        headers = {"Retry-After": "1"} if status == 429 else {}
        return httpx2.Response(status, json=body, headers=headers)

    def handle(self, url: str) -> tuple[int, Resource]:
        self.requests.append(url)
        if self.fail_next:
            status = self.fail_next.pop(0)
            return status, {"resourceType": "OperationOutcome", "issue": [{"severity": "error"}]}
        parts = urlsplit(url)
        segments = [s for s in parts.path.split("/") if s]
        query = {k: v[-1] for k, v in parse_qs(parts.query).items()}
        if len(segments) >= 2 and segments[-2][:1].isupper():
            resource_type, resource_id = segments[-2], segments[-1]
            found = [
                r
                for r in self._resources
                if r["resourceType"] == resource_type and r["id"] == resource_id
            ]
            if not found:
                return 404, {"resourceType": "OperationOutcome"}
            return 200, copy.deepcopy(found[0])
        if not segments:
            return 404, {"resourceType": "OperationOutcome"}
        return 200, self._search(
            segments[-1], query, base=f"{parts.scheme}://{parts.netloc}{parts.path}"
        )

    def _search(self, resource_type: str, query: dict[str, str], *, base: str) -> Resource:
        matches = [r for r in self._resources if r["resourceType"] == resource_type]
        since = query.get("_lastUpdated", "")
        if since.startswith("gt"):
            cutoff = dt.datetime.fromisoformat(since[2:])
            matches = [r for r in matches if _updated(r) > cutoff]
        if "code" in query:
            wanted = {c.split("|")[-1] for c in query["code"].split(",")}
            matches = [
                r
                for r in matches
                if wanted & {c.get("code") for c in r.get("code", {}).get("coding", [])}
            ]
        matches.sort(key=_updated)
        size = int(query.get("_count", DEFAULT_PAGE))
        offset = int(query.get("_offset", 0))
        page = matches[offset : offset + size]
        links = [{"relation": "self", "url": f"{base}?{urlencode(query)}"}]
        if offset + size < len(matches):
            links.append(
                {
                    "relation": "next",
                    "url": f"{base}?{urlencode(query | {'_offset': str(offset + size)})}",
                }
            )
        return {
            "resourceType": "Bundle",
            "type": "searchset",
            "total": len(matches),
            "link": links,
            "entry": [{"resource": copy.deepcopy(r)} for r in page],
        }
