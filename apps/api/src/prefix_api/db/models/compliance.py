"""Go-live compliance (HIPAA, India DPDP Act 2023): consent history and security incidents."""

import datetime as dt
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prefix_api.db.base import Base, TenantScoped, Timestamps, UUIDPrimaryKey
from prefix_api.db.types import EncryptedString, str_enum
from prefix_api.domain.enums import (
    ConsentPurpose,
    ConsentSource,
    IncidentSeverity,
    IncidentStatus,
)


class ConsentRecord(UUIDPrimaryKey, TenantScoped, Base):
    """One grant or withdrawal of a patient's consent, kept as evidence.

    Append-only: a database trigger rejects UPDATE and DELETE. ``Patient.consent_to_message``
    holds the current answer; this table holds who changed it, how, and when.
    """

    __tablename__ = "consent_record"
    __table_args__ = (Index("ix_consent_record_patient_recorded", "patient_id", "recorded_at"),)

    patient_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("patient.id", ondelete="CASCADE")
    )
    purpose: Mapped[ConsentPurpose] = mapped_column(str_enum(ConsentPurpose, "consent_purpose"))
    granted: Mapped[bool]
    source: Mapped[ConsentSource] = mapped_column(str_enum(ConsentSource, "consent_source"))
    recorded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("app_user.id", ondelete="SET NULL")
    )
    recorded_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class SecurityIncident(UUIDPrimaryKey, Timestamps, TenantScoped, Base):
    """A suspected or confirmed personal-data breach (docs/BREACH_RESPONSE.md).

    ``notify_by`` is 72 hours after discovery: the deadline for telling the authority
    (India's Data Protection Board; for HIPAA, the covered entity's own process applies).
    """

    __tablename__ = "security_incident"

    # A short label without patient data, e.g. "Laptop with export lost".
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(EncryptedString("security_incident.description"))
    severity: Mapped[IncidentSeverity] = mapped_column(
        str_enum(IncidentSeverity, "incident_severity")
    )
    status: Mapped[IncidentStatus] = mapped_column(
        str_enum(IncidentStatus, "incident_status"), default=IncidentStatus.OPEN, index=True
    )
    # Kinds of data involved, e.g. ["contact", "health"]; never the data itself.
    data_categories: Mapped[list[str]] = mapped_column(JSONB, default=list)
    discovered_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    notify_by: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    authority_notified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    patients_notified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    reported_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("app_user.id", ondelete="SET NULL")
    )


class SecurityIncidentPatient(UUIDPrimaryKey, TenantScoped, Base):
    """A patient whose data an incident may have exposed. Their records are kept from the
    retention purge while the incident is open."""

    __tablename__ = "security_incident_patient"
    __table_args__ = (UniqueConstraint("incident_id", "patient_id"),)

    incident_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("security_incident.id", ondelete="CASCADE"), index=True
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("patient.id", ondelete="CASCADE"), index=True
    )
    added_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
