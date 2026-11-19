"""A patient's data rights: a full export (access) and erasure (DPDP Act s.11-12, HIPAA).

Both are admin-only and audited. Erasure is double-confirmed: the caller must acknowledge it
cannot be undone *and* type the patient's erasure phrase.

Erasure removes every identifying value and free text the hospital holds about the patient:
names, date of birth, phone, e-mail and MRN; their lookup hashes; the bodies of messages,
check-in replies, assistant questions and answers, escalation and notification summaries;
cancellation notes; and unused portal links. Ids, dates, statuses and step outcomes stay, so
analytics and the audit trail keep working on de-identified rows. The audit log itself only
ever holds ids, never patient data, so it is kept whole.

Stored values are AES-GCM ciphertexts under the organization's key. Overwriting them removes
the live copy; older copies in database backups stay readable to the key holder until those
backups expire. Making backups unreadable too needs per-patient keys in a key-management
service (docs/COMPLIANCE.md, "Erasure and backups").
"""

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.orm import Session

from prefix_api.db.models import (
    Appointment,
    AssistantExchange,
    CheckIn,
    ConsentRecord,
    Escalation,
    Facility,
    Message,
    Organization,
    Patient,
    PatientMagicLink,
    PatientTimelineEvent,
    PrepStep,
    ProcedureType,
    StaffNotification,
    WaitlistEntry,
)
from prefix_api.domain.enums import AppointmentStatus, AuditAction, ConsentSource
from prefix_api.domain.errors import NotFoundError, ValidationFailedError
from prefix_api.domain.permissions import Permission
from prefix_api.domain.principal import Principal, StaffPrincipal
from prefix_api.repositories import patients as patients_repo
from prefix_api.services.audit import record_audit
from prefix_api.services.consent import append_consent

EXPORT_FORMAT_VERSION = 1
ERASED = "[erased]"
ERASED_DOB = dt.date(1900, 1, 1)
_LIVE = (AppointmentStatus.SCHEDULED, AppointmentStatus.READY, AppointmentStatus.AT_RISK)


def erasure_phrase(patient_id: uuid.UUID) -> str:
    """What the admin must type to confirm. Tied to this patient, so a slip cannot erase another."""
    return f"ERASE {patient_id.hex[:8].upper()}"


@dataclass(frozen=True, slots=True)
class ErasureResult:
    patient_id: uuid.UUID
    erased_at: dt.datetime
    rows_changed: int


def _iso(value: dt.datetime | dt.date | None) -> str | None:
    return None if value is None else value.isoformat()


class DataRightsService:
    def __init__(self, session: Session) -> None:
        self._session = session

    # Access ---------------------------------------------------------------------------------

    def export(
        self, principal: StaffPrincipal, patient_id: uuid.UUID, *, now: dt.datetime, fmt: str
    ) -> dict[str, Any]:
        """Everything held about one patient, for a subject-access request."""
        principal.require(Permission.PATIENT_EXPORT)
        patient = self._patient(principal, patient_id)
        data = self._collect(patient, now=now)
        record_audit(
            self._session,
            principal=principal,
            action=AuditAction.EXPORT,
            entity_type="patient_export",
            entity_id=patient.id,
            patient_id=patient.id,
            purpose="data_subject_access",
            details={
                "status": fmt,
                "count": sum(len(v) for v in data.values() if isinstance(v, list)),
            },
        )
        return data

    def _collect(self, patient: Patient, *, now: dt.datetime) -> dict[str, Any]:
        s = self._session
        org = s.get_one(Organization, patient.organization_id)
        appointments = s.execute(
            select(Appointment, ProcedureType.name, Facility.name)
            .join(ProcedureType, ProcedureType.id == Appointment.procedure_type_id)
            .join(Facility, Facility.id == Appointment.facility_id)
            .where(Appointment.patient_id == patient.id)
            .order_by(Appointment.scheduled_at)
        ).all()
        timeline = s.execute(
            select(PatientTimelineEvent, PrepStep.title)
            .join(PrepStep, PrepStep.id == PatientTimelineEvent.step_id)
            .where(PatientTimelineEvent.patient_id == patient.id)
            .order_by(PatientTimelineEvent.due_at)
        ).all()
        by_patient = {"patient_id": patient.id}
        check_ins = s.scalars(select(CheckIn).filter_by(**by_patient).order_by(CheckIn.created_at))
        messages = s.scalars(select(Message).filter_by(**by_patient).order_by(Message.created_at))
        exchanges = s.scalars(
            select(AssistantExchange).filter_by(**by_patient).order_by(AssistantExchange.created_at)
        )
        escalations = s.scalars(
            select(Escalation).filter_by(**by_patient).order_by(Escalation.created_at)
        )
        consents = s.scalars(
            select(ConsentRecord).filter_by(**by_patient).order_by(ConsentRecord.recorded_at)
        )
        waitlist = s.scalars(
            select(WaitlistEntry).filter_by(**by_patient).order_by(WaitlistEntry.created_at)
        )
        return {
            "export": {
                "format_version": EXPORT_FORMAT_VERSION,
                "generated_at": now.isoformat(),
                "organization": org.name,
                "purpose": "Copy of the personal data held about this patient",
            },
            "patient": {
                "id": str(patient.id),
                "first_name": patient.first_name,
                "last_name": patient.last_name,
                "date_of_birth": _iso(patient.date_of_birth),
                "phone": patient.phone,
                "email": patient.email,
                "mrn": patient.mrn,
                "preferred_language": patient.preferred_language,
                "preferred_channel": patient.preferred_channel.value,
                "consent_to_message": patient.consent_to_message,
                "consent_timestamp": _iso(patient.consent_timestamp),
                "on_anticoagulant": patient.on_anticoagulant,
                "diabetic_on_insulin": patient.diabetic_on_insulin,
                "on_glp1_agonist": patient.on_glp1_agonist,
                "has_contrast_allergy": patient.has_contrast_allergy,
                "egfr_value": None if patient.egfr_value is None else float(patient.egfr_value),
                "has_metal_implant": patient.has_metal_implant,
                "erased_at": _iso(patient.erased_at),
            },
            "consent_history": [
                {
                    "purpose": c.purpose.value,
                    "granted": c.granted,
                    "source": c.source.value,
                    "recorded_at": _iso(c.recorded_at),
                }
                for c in consents
            ],
            "appointments": [
                {
                    "id": str(a.id),
                    "procedure": procedure,
                    "facility": facility,
                    "scheduled_at": _iso(a.scheduled_at),
                    "status": a.status.value,
                    "cancellation_reason": a.cancellation_reason.value
                    if a.cancellation_reason
                    else None,
                    "cancellation_note": a.cancellation_note,
                }
                for a, procedure, facility in appointments
            ],
            "prep_timeline": [
                {
                    "appointment_id": str(e.appointment_id),
                    "step": title,
                    "due_at": _iso(e.due_at),
                    "status": e.status.value,
                    "confirmed_at": _iso(e.confirmed_at),
                }
                for e, title in timeline
            ],
            "check_ins": [
                {
                    "question": c.question,
                    "response": c.response.value if c.response else None,
                    "response_text": c.response_text,
                    "answered_at": _iso(c.answered_at),
                }
                for c in check_ins
            ],
            "messages": [
                {
                    "channel": m.channel.value,
                    "direction": m.direction.value,
                    "status": m.status.value,
                    "body": m.body,
                    "sent_at": _iso(m.sent_at),
                    "created_at": _iso(m.created_at),
                }
                for m in messages
            ],
            "assistant_questions": [
                {
                    "question": x.question,
                    "reply": x.reply,
                    "outcome": x.outcome.value,
                    "asked_at": _iso(x.created_at),
                }
                for x in exchanges
            ],
            "escalations": [
                {
                    "summary": e.summary,
                    "severity": e.severity.value,
                    "status": e.status.value,
                    "created_at": _iso(e.created_at),
                    "resolved_at": _iso(e.resolved_at),
                }
                for e in escalations
            ],
            "waitlist": [
                {
                    "earliest_date": _iso(w.earliest_date),
                    "latest_date": _iso(w.latest_date),
                    "status": w.status.value,
                }
                for w in waitlist
            ],
        }

    # Erasure --------------------------------------------------------------------------------

    def erase(
        self,
        principal: StaffPrincipal,
        patient_id: uuid.UUID,
        *,
        acknowledged: bool,
        phrase: str,
        now: dt.datetime,
    ) -> ErasureResult:
        principal.require(Permission.PATIENT_ERASE)
        patient = self._patient(principal, patient_id)
        if not acknowledged:
            raise ValidationFailedError("Confirm that erasure cannot be undone")
        if phrase.strip().upper() != erasure_phrase(patient.id):
            raise ValidationFailedError(
                f"Type {erasure_phrase(patient.id)} exactly to confirm the erasure"
            )
        upcoming = self._session.scalar(
            select(Appointment.id)
            .where(
                Appointment.patient_id == patient.id,
                Appointment.scheduled_at >= now,
                Appointment.status.in_(_LIVE),
            )
            .limit(1)
        )
        if upcoming is not None:
            raise ValidationFailedError(
                "Cancel or complete this patient's upcoming procedures before erasing their data"
            )
        return erase_patient(self._session, patient, principal=principal, now=now, reason="request")

    def _patient(self, principal: StaffPrincipal, patient_id: uuid.UUID) -> Patient:
        patient = patients_repo.get_patient(self._session, principal.organization_id, patient_id)
        if patient is None:
            raise NotFoundError("Patient not found")
        return patient


def erase_patient(
    session: Session,
    patient: Patient,
    *,
    principal: Principal,
    now: dt.datetime,
    reason: str,
) -> ErasureResult:
    """Remove a patient's identifying data and free text. Shared by requests and retention."""
    if patient.erased_at is not None:
        raise ValidationFailedError("This patient's data has already been erased")
    if patient.consent_to_message:
        # The consent history keeps its own record of the withdrawal.
        append_consent(
            session,
            patient,
            granted=False,
            source=ConsentSource.ERASURE,
            principal=principal,
            now=now,
        )
    pid = patient.id
    patient.first_name = ERASED
    patient.last_name = ERASED
    patient.date_of_birth = ERASED_DOB
    patient.phone = patient.email = patient.mrn = None
    patient.phone_bidx = patient.email_bidx = patient.mrn_bidx = None
    patient.consent_to_message = False
    patient.consent_timestamp = now
    patient.on_anticoagulant = patient.diabetic_on_insulin = patient.on_glp1_agonist = False
    patient.has_contrast_allergy = patient.has_metal_implant = False
    patient.egfr_value = None
    patient.erased_at = now
    changed = 1

    statements = [
        update(Message)
        .where(Message.patient_id == pid)
        .values(body=ERASED, original_body=None, error=None),
        update(CheckIn).where(CheckIn.patient_id == pid).values(response_text=None),
        update(AssistantExchange)
        .where(AssistantExchange.patient_id == pid)
        .values(question=ERASED, reply=ERASED, reason=None, redactions={}),
        update(Escalation)
        .where(Escalation.patient_id == pid)
        .values(summary=ERASED, resolution_note=None),
        update(StaffNotification).where(StaffNotification.patient_id == pid).values(summary=ERASED),
        update(Appointment)
        .where(Appointment.patient_id == pid)
        .values(cancellation_note=None, external_id=None),
        delete(PatientMagicLink).where(PatientMagicLink.patient_id == pid),
    ]
    for statement in statements:
        result = session.execute(statement.execution_options(synchronize_session=False))
        changed += cast(CursorResult[Any], result).rowcount
    session.flush()
    record_audit(
        session,
        principal=principal,
        action=AuditAction.DELETE,
        entity_type="patient_erasure",
        entity_id=pid,
        patient_id=pid,
        organization_id=patient.organization_id,
        purpose="data_erasure",
        details={"reason": reason, "count": changed},
    )
    return ErasureResult(patient_id=pid, erased_at=now, rows_changed=changed)
