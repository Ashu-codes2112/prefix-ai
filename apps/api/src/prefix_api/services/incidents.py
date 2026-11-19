"""Security incidents and personal-data breaches (docs/BREACH_RESPONSE.md).

An admin records what happened and when it was discovered; the deadline for telling the
authority is 72 hours after discovery. Affected patients are added by MRN (looked up through
its keyed hash, never stored in the incident). The export for the authority carries the facts
a regulator asks for and counts of affected people, never their identities.
"""

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from prefix_api.core.crypto import get_blind_indexer, normalize_identifier
from prefix_api.db.models import (
    Organization,
    Patient,
    SecurityIncident,
    SecurityIncidentPatient,
)
from prefix_api.domain.enums import AuditAction, IncidentSeverity, IncidentStatus
from prefix_api.domain.errors import NotFoundError, ValidationFailedError
from prefix_api.domain.permissions import Permission
from prefix_api.domain.principal import StaffPrincipal
from prefix_api.services.audit import record_audit

ENTITY = "security_incident"
NOTIFY_WITHIN = dt.timedelta(hours=72)
DATA_CATEGORIES = frozenset({"identity", "contact", "health", "messages", "credentials"})


@dataclass(frozen=True, slots=True)
class IncidentView:
    incident: SecurityIncident
    affected_patients: int


@dataclass(frozen=True, slots=True)
class AddPatientsResult:
    added: int
    already_listed: int
    not_found: int


def _categories(values: list[str]) -> list[str]:
    unknown = sorted(set(values) - DATA_CATEGORIES)
    if unknown:
        raise ValidationFailedError(
            "Unknown data categories: "
            + ", ".join(unknown)
            + ". Use: "
            + ", ".join(sorted(DATA_CATEGORIES))
        )
    return sorted(set(values))


class IncidentService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        principal: StaffPrincipal,
        *,
        title: str,
        description: str,
        severity: IncidentSeverity,
        discovered_at: dt.datetime,
        data_categories: list[str],
        now: dt.datetime,
    ) -> IncidentView:
        principal.require(Permission.INCIDENT_MANAGE)
        if discovered_at > now + dt.timedelta(minutes=5):
            raise ValidationFailedError("The discovery time cannot be in the future")
        incident = SecurityIncident(
            organization_id=principal.organization_id,
            title=title.strip(),
            description=description.strip(),
            severity=severity,
            status=IncidentStatus.OPEN,
            data_categories=_categories(data_categories),
            discovered_at=discovered_at,
            notify_by=discovered_at + NOTIFY_WITHIN,
            reported_by_user_id=principal.user_id,
        )
        self._session.add(incident)
        self._session.flush()
        self._audit(principal, AuditAction.CREATE, incident, status=incident.status.value)
        return IncidentView(incident, 0)

    def list_incidents(self, principal: StaffPrincipal) -> list[IncidentView]:
        principal.require(Permission.INCIDENT_MANAGE)
        counts = (
            select(
                SecurityIncidentPatient.incident_id,
                func.count().label("n"),
            )
            .group_by(SecurityIncidentPatient.incident_id)
            .subquery()
        )
        rows = self._session.execute(
            select(SecurityIncident, func.coalesce(counts.c.n, 0))
            .outerjoin(counts, counts.c.incident_id == SecurityIncident.id)
            .where(SecurityIncident.organization_id == principal.organization_id)
            .order_by(SecurityIncident.discovered_at.desc())
        ).all()
        record_audit(
            self._session,
            principal=principal,
            action=AuditAction.LIST,
            entity_type=ENTITY,
            purpose="breach_response",
            details={"count": len(rows)},
        )
        return [IncidentView(incident, int(n)) for incident, n in rows]

    def get(self, principal: StaffPrincipal, incident_id: uuid.UUID) -> IncidentView:
        principal.require(Permission.INCIDENT_MANAGE)
        incident = self._incident(principal, incident_id)
        self._audit(principal, AuditAction.READ, incident)
        return IncidentView(incident, self._count(incident.id))

    def add_patients_by_mrn(
        self,
        principal: StaffPrincipal,
        incident_id: uuid.UUID,
        mrns: list[str],
        *,
        now: dt.datetime,
    ) -> AddPatientsResult:
        principal.require(Permission.INCIDENT_MANAGE)
        incident = self._incident(principal, incident_id)
        indexer = get_blind_indexer()
        wanted = {indexer.index(normalize_identifier(m), "mrn") for m in mrns if m.strip()}
        if not wanted:
            raise ValidationFailedError("List at least one MRN")
        found = list(
            self._session.scalars(
                select(Patient.id).where(
                    Patient.organization_id == principal.organization_id,
                    Patient.mrn_bidx.in_(wanted),
                )
            )
        )
        listed = set(
            self._session.scalars(
                select(SecurityIncidentPatient.patient_id).where(
                    SecurityIncidentPatient.incident_id == incident.id
                )
            )
        )
        new = [pid for pid in found if pid not in listed]
        self._session.add_all(
            SecurityIncidentPatient(
                organization_id=principal.organization_id,
                incident_id=incident.id,
                patient_id=pid,
                added_at=now,
            )
            for pid in new
        )
        self._session.flush()
        for pid in new:
            record_audit(
                self._session,
                principal=principal,
                action=AuditAction.UPDATE,
                entity_type=ENTITY,
                entity_id=incident.id,
                patient_id=pid,
                purpose="breach_response",
                details={"status": "patient_added"},
            )
        return AddPatientsResult(
            added=len(new), already_listed=len(found) - len(new), not_found=len(wanted) - len(found)
        )

    def advance(
        self, principal: StaffPrincipal, incident_id: uuid.UUID, step: str, *, now: dt.datetime
    ) -> IncidentView:
        """Record progress: contained, authority notified, patients notified, closed."""
        principal.require(Permission.INCIDENT_MANAGE)
        incident = self._incident(principal, incident_id)
        if incident.status == IncidentStatus.CLOSED:
            raise ValidationFailedError("This incident is closed")
        match step:
            case "contained":
                incident.status = IncidentStatus.CONTAINED
            case "authority_notified":
                incident.authority_notified_at = now
                incident.status = IncidentStatus.NOTIFIED
            case "patients_notified":
                incident.patients_notified_at = now
            case "closed":
                incident.status = IncidentStatus.CLOSED
                incident.closed_at = now
            case _:
                raise ValidationFailedError(f"Unknown step '{step}'")
        self._session.flush()
        self._audit(principal, AuditAction.UPDATE, incident, status=step)
        return IncidentView(incident, self._count(incident.id))

    def authority_report(
        self, principal: StaffPrincipal, incident_id: uuid.UUID, *, now: dt.datetime
    ) -> dict[str, Any]:
        """What a regulator asks for: nature, extent, timing and response. No identities."""
        principal.require(Permission.INCIDENT_MANAGE)
        incident = self._incident(principal, incident_id)
        org = self._session.get_one(Organization, incident.organization_id)
        affected = self._count(incident.id)
        record_audit(
            self._session,
            principal=principal,
            action=AuditAction.EXPORT,
            entity_type=ENTITY,
            entity_id=incident.id,
            purpose="breach_notification",
            details={"count": affected},
        )

        def iso(value: dt.datetime | None) -> str | None:
            return None if value is None else value.isoformat()

        notified = incident.authority_notified_at
        return {
            "report_generated_at": now.isoformat(),
            "organization": {"name": org.name, "country": org.country_code},
            "incident": {
                "reference": str(incident.id),
                "title": incident.title,
                "description": incident.description,
                "severity": incident.severity.value,
                "status": incident.status.value,
                "data_categories": incident.data_categories,
            },
            "timeline": {
                "discovered_at": iso(incident.discovered_at),
                "notify_authority_by": iso(incident.notify_by),
                "authority_notified_at": iso(notified),
                "notified_within_72_hours": None
                if notified is None
                else notified <= incident.notify_by,
                "patients_notified_at": iso(incident.patients_notified_at),
                "closed_at": iso(incident.closed_at),
            },
            "affected_patients": affected,
        }

    def _incident(self, principal: StaffPrincipal, incident_id: uuid.UUID) -> SecurityIncident:
        incident = self._session.get(SecurityIncident, incident_id)
        if incident is None or incident.organization_id != principal.organization_id:
            raise NotFoundError("Incident not found")
        return incident

    def _count(self, incident_id: uuid.UUID) -> int:
        return int(
            self._session.scalar(
                select(func.count()).where(SecurityIncidentPatient.incident_id == incident_id)
            )
            or 0
        )

    def _audit(
        self,
        principal: StaffPrincipal,
        action: AuditAction,
        incident: SecurityIncident,
        *,
        status: str | None = None,
    ) -> None:
        record_audit(
            self._session,
            principal=principal,
            action=action,
            entity_type=ENTITY,
            entity_id=incident.id,
            purpose="breach_response",
            details={"status": status} if status else None,
        )
