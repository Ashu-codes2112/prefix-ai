"""The FHIR connection, its health and runs, and webhook endpoints. No patient data."""

import datetime as dt
import uuid
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from prefix_api.db.models import IntegrationSyncRun, WebhookDelivery, WebhookEndpoint
from prefix_api.domain.enums import DeliveryStatus, SyncStatus
from prefix_api.services.integrations import Health, IntegrationHealth

Url = Annotated[str, StringConstraints(strip_whitespace=True, min_length=8, max_length=500)]


class FhirMappingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # EHR service or appointment-type code -> our procedure type code,
    # e.g. "73761001": "COLONOSCOPY".
    service_codes: dict[str, str] = Field(default_factory=dict, max_length=200)
    # FHIR Location id -> our facility id.
    locations: dict[str, uuid.UUID] = Field(default_factory=dict, max_length=200)
    default_facility_id: uuid.UUID | None = None


class FhirConnectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: Url
    # Leave out to keep the stored token; send "" to remove it.
    token: Annotated[str, StringConstraints(max_length=4000)] | None = None
    enabled: bool = True
    mapping: FhirMappingIn = Field(default_factory=FhirMappingIn)


class SyncRunOut(BaseModel):
    id: uuid.UUID
    started_at: dt.datetime
    finished_at: dt.datetime | None
    status: SyncStatus
    counts: dict[str, dict[str, int]]
    problems: list[str]
    error: str | None

    @classmethod
    def build(cls, run: IntegrationSyncRun) -> SyncRunOut:
        return cls(
            id=run.id,
            started_at=run.started_at,
            finished_at=run.finished_at,
            status=run.status,
            counts=run.counts,
            problems=run.problems,
            error=run.error,
        )


class FhirConnectionOut(BaseModel):
    base_url: str
    enabled: bool
    has_token: bool
    mapping: dict[str, Any]
    cursors: dict[str, str]
    last_sync_started_at: dt.datetime | None
    last_success_at: dt.datetime | None
    last_error: str | None
    consecutive_failures: int


class WebhookEndpointOut(BaseModel):
    id: uuid.UUID
    url: str
    description: str
    enabled: bool
    created_at: dt.datetime

    @classmethod
    def build(cls, endpoint: WebhookEndpoint) -> WebhookEndpointOut:
        return cls(
            id=endpoint.id,
            url=endpoint.url,
            description=endpoint.description,
            enabled=endpoint.enabled,
            created_at=endpoint.created_at,
        )


class WebhookDeliveryOut(BaseModel):
    id: uuid.UUID
    endpoint_id: uuid.UUID
    event: str
    status: DeliveryStatus
    attempts: int
    created_at: dt.datetime
    next_attempt_at: dt.datetime
    last_status_code: int | None
    last_error: str | None
    delivered_at: dt.datetime | None
    # The appointment's new status, from the payload (ids and statuses only).
    appointment_status: str | None

    @classmethod
    def build(cls, delivery: WebhookDelivery) -> WebhookDeliveryOut:
        data = delivery.payload.get("data", {})
        return cls(
            id=delivery.id,
            endpoint_id=delivery.endpoint_id,
            event=delivery.event,
            status=delivery.status,
            attempts=delivery.attempts,
            created_at=delivery.created_at,
            next_attempt_at=delivery.next_attempt_at,
            last_status_code=delivery.last_status_code,
            last_error=delivery.last_error,
            delivered_at=delivery.delivered_at,
            appointment_status=data.get("status") if isinstance(data, dict) else None,
        )


class DeliveryCountsOut(BaseModel):
    pending: int
    delivered: int
    failed: int


class IntegrationHealthOut(BaseModel):
    status: Health
    fhir: FhirConnectionOut | None
    runs: list[SyncRunOut]
    webhooks: list[WebhookEndpointOut]
    deliveries: DeliveryCountsOut
    recent_deliveries: list[WebhookDeliveryOut]

    @classmethod
    def build(cls, health: IntegrationHealth) -> IntegrationHealthOut:
        c = health.connection
        return cls(
            status=health.status,
            fhir=None
            if c is None
            else FhirConnectionOut(
                base_url=c.base_url,
                enabled=c.enabled,
                has_token=bool(c.auth_token),
                mapping=c.mapping,
                cursors=c.cursors,
                last_sync_started_at=c.last_sync_started_at,
                last_success_at=c.last_success_at,
                last_error=c.last_error,
                consecutive_failures=c.consecutive_failures,
            ),
            runs=[SyncRunOut.build(r) for r in health.runs],
            webhooks=[WebhookEndpointOut.build(e) for e in health.endpoints],
            deliveries=DeliveryCountsOut(
                pending=health.deliveries.pending,
                delivered=health.deliveries.delivered,
                failed=health.deliveries.failed,
            ),
            recent_deliveries=[WebhookDeliveryOut.build(d) for d in health.recent_deliveries],
        )


class WebhookEndpointIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: Url
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""


class WebhookEndpointCreatedOut(BaseModel):
    endpoint: WebhookEndpointOut
    # Shown once. Use it to check X-Prefix-Signature on every delivery.
    secret: str


class WebhookEnabledIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
