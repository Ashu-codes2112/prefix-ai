"""Admin view of the EHR integration: the FHIR connection, its health, and webhook endpoints.

Everything here is configuration, not patient data; changes are still audited. Secrets (the
FHIR token, webhook signing secrets) are stored encrypted and never returned, except a new
webhook secret, shown once when the endpoint is created.
"""

import datetime as dt
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from prefix_api.db.models import (
    Facility,
    IntegrationConnection,
    IntegrationSyncRun,
    ProcedureType,
    WebhookDelivery,
    WebhookEndpoint,
)
from prefix_api.domain.enums import AuditAction, DeliveryStatus
from prefix_api.domain.errors import NotFoundError, ValidationFailedError
from prefix_api.domain.permissions import Permission
from prefix_api.domain.principal import StaffPrincipal
from prefix_api.services.audit import record_audit
from prefix_api.services.fhir.client import FhirClient
from prefix_api.services.fhir.sync import FhirSync, iter_due_connections
from prefix_api.services.webhooks import new_secret

type ClientFactory = Callable[[IntegrationConnection], FhirClient]
type Health = Literal["off", "healthy", "failing", "stale", "never_run"]

# A sync runs every 15 minutes; an hour without success means something is wrong.
STALE_AFTER = dt.timedelta(hours=1)
RECENT = 10


def default_client(connection: IntegrationConnection) -> FhirClient:
    return FhirClient(connection.base_url, token=connection.auth_token)


@dataclass(frozen=True, slots=True)
class DeliveryCounts:
    pending: int
    delivered: int
    failed: int


@dataclass(frozen=True, slots=True)
class IntegrationHealth:
    status: Health
    connection: IntegrationConnection | None
    runs: list[IntegrationSyncRun]
    endpoints: list[WebhookEndpoint]
    deliveries: DeliveryCounts
    recent_deliveries: list[WebhookDelivery]


def health_of(connection: IntegrationConnection | None, now: dt.datetime) -> Health:
    if connection is None or not connection.enabled:
        return "off"
    if connection.consecutive_failures > 0:
        return "failing"
    if connection.last_success_at is None:
        return "never_run"
    if now - connection.last_success_at > STALE_AFTER:
        return "stale"
    return "healthy"


def _check_url(url: str, *, production: bool, what: str) -> str:
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValidationFailedError(f"The {what} must be a full http(s) address")
    if production and parts.scheme != "https":
        raise ValidationFailedError(f"The {what} must use https in production")
    if parts.username or parts.password:
        raise ValidationFailedError(f"Put credentials for the {what} in the token, not the address")
    return url


class IntegrationService:
    def __init__(
        self, session: Session, *, production: bool, client_factory: ClientFactory = default_client
    ) -> None:
        self._session = session
        self._production = production
        self._client_factory = client_factory

    def _connection(self, org_id: uuid.UUID) -> IntegrationConnection | None:
        return self._session.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.organization_id == org_id,
                IntegrationConnection.kind == "fhir",
            )
        )

    def health(self, principal: StaffPrincipal, *, now: dt.datetime) -> IntegrationHealth:
        principal.require(Permission.INTEGRATION_MANAGE)
        org = principal.organization_id
        connection = self._connection(org)
        runs = (
            list(
                self._session.scalars(
                    select(IntegrationSyncRun)
                    .where(IntegrationSyncRun.connection_id == connection.id)
                    .order_by(IntegrationSyncRun.started_at.desc())
                    .limit(RECENT)
                )
            )
            if connection
            else []
        )
        endpoints = list(
            self._session.scalars(
                select(WebhookEndpoint)
                .where(WebhookEndpoint.organization_id == org)
                .order_by(WebhookEndpoint.created_at)
            )
        )
        rows = self._session.execute(
            select(WebhookDelivery.status, func.count())
            .where(WebhookDelivery.organization_id == org)
            .group_by(WebhookDelivery.status)
        )
        # Not dict(rows): a Result has .keys(), so dict() would treat it as a mapping.
        counts: dict[DeliveryStatus, int] = {status: n for status, n in rows}  # noqa: C416
        recent = list(
            self._session.scalars(
                select(WebhookDelivery)
                .where(WebhookDelivery.organization_id == org)
                .order_by(WebhookDelivery.created_at.desc())
                .limit(RECENT)
            )
        )
        return IntegrationHealth(
            status=health_of(connection, now),
            connection=connection,
            runs=runs,
            endpoints=endpoints,
            deliveries=DeliveryCounts(
                pending=counts.get(DeliveryStatus.PENDING, 0),
                delivered=counts.get(DeliveryStatus.DELIVERED, 0),
                failed=counts.get(DeliveryStatus.FAILED, 0),
            ),
            recent_deliveries=recent,
        )

    def configure_fhir(
        self,
        principal: StaffPrincipal,
        *,
        base_url: str,
        token: str | None,
        enabled: bool,
        mapping: dict[str, Any],
    ) -> IntegrationConnection:
        """``token`` None keeps the stored one; an empty string removes it."""
        principal.require(Permission.INTEGRATION_MANAGE)
        org = principal.organization_id
        url = _check_url(base_url, production=self._production, what="FHIR base URL")
        clean = self._check_mapping(org, mapping)
        connection = self._connection(org)
        if connection is None:
            connection = IntegrationConnection(organization_id=org, kind="fhir", cursors={})
            self._session.add(connection)
        elif connection.base_url != url:
            connection.cursors = {}  # a different server: read it from the start
        connection.base_url = url
        connection.enabled = enabled
        connection.mapping = clean
        if token is not None:
            connection.auth_token = token or None
        self._session.flush()
        record_audit(
            self._session,
            principal=principal,
            action=AuditAction.UPDATE,
            entity_type="integration_connection",
            entity_id=connection.id,
            purpose="integration_setup",
            details={"status": "enabled" if enabled else "disabled"},
        )
        return connection

    def _check_mapping(self, org: uuid.UUID, mapping: dict[str, Any]) -> dict[str, Any]:
        codes = {
            str(k): str(v)
            for k, v in dict(mapping.get("service_codes") or {}).items()
            if str(k).strip()
        }
        known = set(
            self._session.scalars(
                select(ProcedureType.code).where(ProcedureType.organization_id == org)
            )
        )
        unknown = sorted(set(codes.values()) - known)
        if unknown:
            raise ValidationFailedError(
                "Unknown procedure types in the mapping: " + ", ".join(unknown)
            )
        facilities = {
            str(f)
            for f in self._session.scalars(
                select(Facility.id).where(Facility.organization_id == org)
            )
        }
        locations = {str(k): str(v) for k, v in dict(mapping.get("locations") or {}).items()}
        default = mapping.get("default_facility_id")
        for facility in [*locations.values(), *([str(default)] if default else [])]:
            if facility not in facilities:
                raise ValidationFailedError(
                    "The mapping names a facility this hospital does not have"
                )
        clean: dict[str, Any] = {"service_codes": codes, "locations": locations}
        if default:
            clean["default_facility_id"] = str(default)
        return clean

    def sync_now(self, principal: StaffPrincipal, *, now: dt.datetime) -> IntegrationSyncRun:
        principal.require(Permission.INTEGRATION_MANAGE)
        connection = self._connection(principal.organization_id)
        if connection is None:
            raise NotFoundError("No FHIR server is set up")
        if not connection.enabled:
            raise ValidationFailedError("The FHIR connection is switched off")
        return FhirSync(self._session, connection, self._client_factory(connection), now=now).run()

    def add_webhook(
        self, principal: StaffPrincipal, *, url: str, description: str
    ) -> tuple[WebhookEndpoint, str]:
        """The new endpoint and its signing secret, which is shown this once."""
        principal.require(Permission.INTEGRATION_MANAGE)
        secret = new_secret()
        endpoint = WebhookEndpoint(
            organization_id=principal.organization_id,
            url=_check_url(url, production=self._production, what="webhook address"),
            secret=secret,
            description=description.strip()[:200],
            enabled=True,
        )
        self._session.add(endpoint)
        self._session.flush()
        record_audit(
            self._session,
            principal=principal,
            action=AuditAction.CREATE,
            entity_type="webhook_endpoint",
            entity_id=endpoint.id,
            purpose="integration_setup",
        )
        return endpoint, secret

    def set_webhook_enabled(
        self, principal: StaffPrincipal, endpoint_id: uuid.UUID, *, enabled: bool
    ) -> WebhookEndpoint:
        principal.require(Permission.INTEGRATION_MANAGE)
        endpoint = self._session.get(WebhookEndpoint, endpoint_id)
        if endpoint is None or endpoint.organization_id != principal.organization_id:
            raise NotFoundError("Webhook endpoint not found")
        endpoint.enabled = enabled
        self._session.flush()
        record_audit(
            self._session,
            principal=principal,
            action=AuditAction.UPDATE,
            entity_type="webhook_endpoint",
            entity_id=endpoint.id,
            purpose="integration_setup",
            details={"status": "enabled" if enabled else "disabled"},
        )
        return endpoint


def sync_all(
    session_factory: Callable[[], Session],
    *,
    now: dt.datetime,
    client_factory: ClientFactory = default_client,
) -> dict[str, int]:
    """The worker's job: one sync per enabled connection, each in its own transaction."""
    with session_factory() as session:
        ids = [c.id for c in iter_due_connections(session)]
    totals = {"connections": len(ids), "failed": 0}
    for connection_id in ids:
        with session_factory() as session, session.begin():
            connection = session.get_one(IntegrationConnection, connection_id)
            run = FhirSync(session, connection, client_factory(connection), now=now).run()
            if run.error:
                totals["failed"] += 1
    return totals
