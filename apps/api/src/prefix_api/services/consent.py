"""Consent to be messaged: recorded, withdrawn, and kept as history (DPDP Act s.6, HIPAA).

``Patient.consent_to_message`` is the current answer, and every send checks it: reminders are
skipped while consent is withdrawn (services/dispatch.py). Each change also appends a
``ConsentRecord``, so the hospital can show who changed it, how and when.
"""

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from prefix_api.db.models import ConsentRecord, Patient
from prefix_api.domain.enums import AuditAction, ConsentPurpose, ConsentSource
from prefix_api.domain.errors import NotFoundError, ValidationFailedError
from prefix_api.domain.permissions import Permission
from prefix_api.domain.principal import Principal, StaffPrincipal
from prefix_api.repositories import patients as patients_repo
from prefix_api.services.audit import record_audit

ENTITY = "consent"


def append_consent(
    session: Session,
    patient: Patient,
    *,
    granted: bool,
    source: ConsentSource,
    principal: Principal,
    now: dt.datetime,
) -> ConsentRecord:
    """Set the patient's current answer and add it to the history, audited as one change."""
    if patient.erased_at is not None:
        raise ValidationFailedError("This patient's data has been erased")
    patient.consent_to_message = granted
    patient.consent_timestamp = now
    record = ConsentRecord(
        organization_id=patient.organization_id,
        patient_id=patient.id,
        purpose=ConsentPurpose.MESSAGING,
        granted=granted,
        source=source,
        recorded_by_user_id=principal.user_id if isinstance(principal, StaffPrincipal) else None,
        recorded_at=now,
    )
    session.add(record)
    session.flush()
    record_audit(
        session,
        principal=principal,
        action=AuditAction.CREATE if granted else AuditAction.UPDATE,
        entity_type=ENTITY,
        entity_id=record.id,
        patient_id=patient.id,
        organization_id=patient.organization_id,
        purpose="consent_management",
        details={"status": "granted" if granted else "withdrawn", "reason": source.value},
    )
    return record


class ConsentService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        principal: StaffPrincipal,
        patient_id: uuid.UUID,
        *,
        granted: bool,
        now: dt.datetime,
    ) -> ConsentRecord:
        """Staff record what the patient told them (in person, on the phone, on a form)."""
        principal.require(Permission.PATIENT_WRITE)
        patient = self._patient(principal, patient_id)
        return append_consent(
            self._session,
            patient,
            granted=granted,
            source=ConsentSource.STAFF,
            principal=principal,
            now=now,
        )

    def history(
        self, principal: StaffPrincipal, patient_id: uuid.UUID
    ) -> tuple[Patient, list[ConsentRecord]]:
        """The patient's current answer (on the returned row) and every change, newest first."""
        principal.require(Permission.PATIENT_READ)
        patient = self._patient(principal, patient_id)
        records = list(
            self._session.scalars(
                select(ConsentRecord)
                .where(
                    ConsentRecord.organization_id == principal.organization_id,
                    ConsentRecord.patient_id == patient.id,
                )
                .order_by(ConsentRecord.recorded_at.desc(), ConsentRecord.id.desc())
            )
        )
        record_audit(
            self._session,
            principal=principal,
            action=AuditAction.LIST,
            entity_type=ENTITY,
            patient_id=patient.id,
            purpose="consent_management",
            details={"count": len(records)},
        )
        return patient, records

    def _patient(self, principal: StaffPrincipal, patient_id: uuid.UUID) -> Patient:
        patient = patients_repo.get_patient(self._session, principal.organization_id, patient_id)
        if patient is None:
            raise NotFoundError("Patient not found")
        return patient
