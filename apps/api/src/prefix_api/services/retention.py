"""Per-organization retention: erase patients once their care here is long finished.

An organization sets ``retention_days`` (none means keep everything, the default). Every night
the worker erases each patient whose latest procedure, or registration when there is none, is
older than that, unless something still needs the record:

- an upcoming procedure, or an active waitlist entry;
- an open security incident that lists the patient (a legal hold).

The dry run reports what would happen, in counts and ids only, and changes nothing.
"""

import datetime as dt
import uuid
from dataclasses import dataclass, field

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from prefix_api.db.models import (
    Appointment,
    Organization,
    Patient,
    SecurityIncident,
    SecurityIncidentPatient,
    WaitlistEntry,
)
from prefix_api.db.session import SessionFactory
from prefix_api.domain.enums import (
    AppointmentStatus,
    AuditAction,
    IncidentStatus,
    WaitlistStatus,
)
from prefix_api.domain.errors import ValidationFailedError
from prefix_api.domain.permissions import Permission
from prefix_api.domain.principal import Principal, StaffPrincipal, SystemPrincipal
from prefix_api.services.audit import record_audit
from prefix_api.services.data_rights import erase_patient

JOB = "retention"
MIN_RETENTION_DAYS = 30
MAX_RETENTION_DAYS = 36_500
_LIVE = (AppointmentStatus.SCHEDULED, AppointmentStatus.READY, AppointmentStatus.AT_RISK)


@dataclass(slots=True)
class RetentionReport:
    retention_days: int | None
    cutoff: dt.datetime | None
    dry_run: bool
    due: list[uuid.UUID] = field(default_factory=list)
    held_for_incident: int = 0
    held_for_upcoming_care: int = 0
    erased: int = 0


def _candidates(
    session: Session, org_id: uuid.UUID, cutoff: dt.datetime, now: dt.datetime
) -> tuple[list[uuid.UUID], int, int]:
    last_procedure = (
        select(func.max(Appointment.scheduled_at))
        .where(Appointment.patient_id == Patient.id)
        .scalar_subquery()
    )
    last_activity = func.coalesce(last_procedure, Patient.created_at)
    old = list(
        session.scalars(
            select(Patient.id)
            .where(
                Patient.organization_id == org_id,
                Patient.erased_at.is_(None),
                last_activity < cutoff,
            )
            .order_by(Patient.id)
        )
    )
    if not old:
        return [], 0, 0
    upcoming = set(
        session.scalars(
            select(Appointment.patient_id).where(
                Appointment.patient_id.in_(old),
                Appointment.scheduled_at >= now,
                Appointment.status.in_(_LIVE),
            )
        )
    ) | set(
        session.scalars(
            select(WaitlistEntry.patient_id).where(
                WaitlistEntry.patient_id.in_(old), WaitlistEntry.status == WaitlistStatus.ACTIVE
            )
        )
    )
    held = set(
        session.scalars(
            select(SecurityIncidentPatient.patient_id).where(
                SecurityIncidentPatient.patient_id.in_(old),
                exists().where(
                    SecurityIncident.id == SecurityIncidentPatient.incident_id,
                    SecurityIncident.status != IncidentStatus.CLOSED,
                ),
            )
        )
    )
    due = [pid for pid in old if pid not in upcoming and pid not in held]
    return due, len(held), len(upcoming - held)


def run_retention(
    session: Session,
    org: Organization,
    *,
    principal: Principal,
    now: dt.datetime,
    dry_run: bool,
) -> RetentionReport:
    days = org.retention_days
    if days is None or org.is_demo:
        return RetentionReport(retention_days=days, cutoff=None, dry_run=dry_run)
    cutoff = now - dt.timedelta(days=days)
    due, held_incident, held_care = _candidates(session, org.id, cutoff, now)
    report = RetentionReport(
        retention_days=days,
        cutoff=cutoff,
        dry_run=dry_run,
        due=due,
        held_for_incident=held_incident,
        held_for_upcoming_care=held_care,
    )
    if not dry_run:
        for pid in due:
            erase_patient(
                session,
                session.get_one(Patient, pid),
                principal=principal,
                now=now,
                reason=JOB,
            )
            report.erased += 1
    record_audit(
        session,
        principal=principal,
        action=AuditAction.READ if dry_run else AuditAction.DELETE,
        entity_type="retention_run",
        organization_id=org.id,
        purpose="data_retention",
        details={
            "status": "dry_run" if dry_run else "purged",
            "count": len(due) if dry_run else report.erased,
        },
    )
    return report


def purge_all(session: Session, *, now: dt.datetime) -> dict[uuid.UUID, int]:
    """The nightly job: every real organization with a retention period. Returns erasures."""
    erased: dict[uuid.UUID, int] = {}
    orgs = session.scalars(
        select(Organization).where(
            Organization.retention_days.is_not(None), Organization.is_demo.is_(False)
        )
    ).all()
    for org in orgs:
        system = SystemPrincipal(organization_id=org.id, job=JOB)
        erased[org.id] = run_retention(
            session, org, principal=system, now=now, dry_run=False
        ).erased
    return erased


def purge_expired(session_factory: SessionFactory, *, now: dt.datetime) -> dict[str, int]:
    """Entry point for the worker's nightly task: one transaction for the whole run."""
    with session_factory() as session, session.begin():
        erased = purge_all(session, now=now)
    return {"organizations": len(erased), "erased": sum(erased.values())}


class RetentionService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def report(self, principal: StaffPrincipal, *, now: dt.datetime) -> RetentionReport:
        principal.require(Permission.RETENTION_MANAGE)
        org = self._session.get_one(Organization, principal.organization_id)
        return run_retention(self._session, org, principal=principal, now=now, dry_run=True)

    def set_policy(self, principal: StaffPrincipal, retention_days: int | None) -> Organization:
        principal.require(Permission.RETENTION_MANAGE)
        if retention_days is not None and not (
            MIN_RETENTION_DAYS <= retention_days <= MAX_RETENTION_DAYS
        ):
            raise ValidationFailedError(
                f"Keep records for {MIN_RETENTION_DAYS} to {MAX_RETENTION_DAYS} days, or forever"
            )
        org = self._session.get_one(Organization, principal.organization_id)
        org.retention_days = retention_days
        self._session.flush()
        record_audit(
            self._session,
            principal=principal,
            action=AuditAction.UPDATE,
            entity_type="retention_policy",
            entity_id=org.id,
            purpose="data_retention",
            details={"count": retention_days},
        )
        return org
