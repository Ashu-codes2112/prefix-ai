"""EHR integration: the FHIR R4 connection and its sync runs, and outbound webhooks."""

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prefix_api.db.base import Base, TenantScoped, Timestamps, UUIDPrimaryKey
from prefix_api.db.types import EncryptedString, str_enum
from prefix_api.domain.enums import DeliveryStatus, SyncStatus


class IntegrationConnection(UUIDPrimaryKey, Timestamps, TenantScoped, Base):
    """One FHIR R4 server a hospital syncs from. At most one per organization."""

    __tablename__ = "integration_connection"
    __table_args__ = (UniqueConstraint("organization_id", "kind"),)

    kind: Mapped[str] = mapped_column(String(20), default="fhir")
    base_url: Mapped[str] = mapped_column(String(500))
    # A bearer token for the FHIR server, if it needs one. Encrypted; never returned.
    auth_token: Mapped[str | None] = mapped_column(
        EncryptedString("integration_connection.auth_token")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # How the EHR's codes map to ours: {"service_codes": {"73761001": "COLONOSCOPY"},
    # "locations": {"<Location id>": "<facility uuid>"}, "default_facility_id": "<uuid>"}.
    mapping: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # Per resource type, the newest meta.lastUpdated already read: {"Patient": "2026-..."}.
    cursors: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)
    last_sync_started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # Short and free of patient data: an HTTP status or a field name.
    last_error: Mapped[str | None] = mapped_column(String(500))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)


class IntegrationSyncRun(UUIDPrimaryKey, TenantScoped, Base):
    """One pass over the FHIR server, with counts per resource type. No patient data."""

    __tablename__ = "integration_sync_run"
    __table_args__ = (Index("ix_integration_sync_run_recent", "connection_id", "started_at"),)

    connection_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("integration_connection.id", ondelete="CASCADE")
    )
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[SyncStatus] = mapped_column(str_enum(SyncStatus, "sync_status"))
    # {"Patient": {"read": 3, "created": 2, "updated": 1, "skipped": 0}, ...}
    counts: Mapped[dict[str, dict[str, int]]] = mapped_column(JSONB, default=dict)
    # Why records were skipped, as field names: ["Appointment: no facility for Location"].
    problems: Mapped[list[str]] = mapped_column(JSONB, default=list)
    error: Mapped[str | None] = mapped_column(String(500))


class WebhookEndpoint(UUIDPrimaryKey, Timestamps, TenantScoped, Base):
    """Where appointment changes are posted, signed with HMAC-SHA256 under ``secret``."""

    __tablename__ = "webhook_endpoint"

    url: Mapped[str] = mapped_column(String(500))
    secret: Mapped[str] = mapped_column(EncryptedString("webhook_endpoint.secret"))
    description: Mapped[str] = mapped_column(String(200), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class WebhookDelivery(UUIDPrimaryKey, TenantScoped, Base):
    """An outbox row: written in the same transaction as the change, sent by the worker."""

    __tablename__ = "webhook_delivery"
    __table_args__ = (
        Index(
            "ix_webhook_delivery_due",
            "next_attempt_at",
            postgresql_where=text("status = 'PENDING'"),
        ),
    )

    endpoint_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("webhook_endpoint.id", ondelete="CASCADE"), index=True
    )
    event: Mapped[str] = mapped_column(String(64))
    # Ids, statuses and times only: never names, contact details or free text.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[DeliveryStatus] = mapped_column(
        str_enum(DeliveryStatus, "delivery_status"), default=DeliveryStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    last_status_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(String(300))
    delivered_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
