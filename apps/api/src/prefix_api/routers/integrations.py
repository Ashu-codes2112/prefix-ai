"""The EHR integration for admins: FHIR connection, health, sync now, and webhooks."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from prefix_api.api.deps import DbSession, Now, SettingsDep, require
from prefix_api.domain.permissions import Permission
from prefix_api.domain.principal import StaffPrincipal
from prefix_api.schemas.integrations import (
    FhirConnectionIn,
    IntegrationHealthOut,
    SyncRunOut,
    WebhookEnabledIn,
    WebhookEndpointCreatedOut,
    WebhookEndpointIn,
    WebhookEndpointOut,
)
from prefix_api.services.integrations import IntegrationService

router = APIRouter(prefix="/integrations", tags=["integrations"])

Admin = Annotated[StaffPrincipal, Depends(require(Permission.INTEGRATION_MANAGE))]


def _service(request: Request, session: DbSession, settings: SettingsDep) -> IntegrationService:
    return IntegrationService(
        session,
        production=settings.is_production,
        client_factory=request.app.state.fhir_client_factory,
    )


Service = Annotated[IntegrationService, Depends(_service)]


@router.get("/health")
def integration_health(principal: Admin, service: Service, now: Now) -> IntegrationHealthOut:
    return IntegrationHealthOut.build(service.health(principal, now=now))


@router.put("/fhir")
def configure_fhir(
    body: FhirConnectionIn, principal: Admin, service: Service, now: Now
) -> IntegrationHealthOut:
    service.configure_fhir(
        principal,
        base_url=body.base_url,
        token=body.token,
        enabled=body.enabled,
        mapping=body.mapping.model_dump(mode="json", exclude_none=True),
    )
    return IntegrationHealthOut.build(service.health(principal, now=now))


@router.post("/fhir/sync")
def sync_now(principal: Admin, service: Service, now: Now) -> SyncRunOut:
    """Run one sync now, as the worker does every 15 minutes."""
    return SyncRunOut.build(service.sync_now(principal, now=now))


@router.post("/webhooks", status_code=status.HTTP_201_CREATED)
def add_webhook(
    body: WebhookEndpointIn, principal: Admin, service: Service
) -> WebhookEndpointCreatedOut:
    endpoint, secret = service.add_webhook(principal, url=body.url, description=body.description)
    return WebhookEndpointCreatedOut(endpoint=WebhookEndpointOut.build(endpoint), secret=secret)


@router.patch("/webhooks/{endpoint_id}")
def set_webhook_enabled(
    endpoint_id: uuid.UUID, body: WebhookEnabledIn, principal: Admin, service: Service
) -> WebhookEndpointOut:
    endpoint = service.set_webhook_enabled(principal, endpoint_id, enabled=body.enabled)
    return WebhookEndpointOut.build(endpoint)
