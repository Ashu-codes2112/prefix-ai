"""Incremental FHIR R4 sync: the EHR's patients, conditions and appointments into Prefix AI.

One run reads, for each resource type, everything updated since the last run's cursor:

1. Patient: matched by the EHR's id, then by MRN; created or updated. A synced patient has
   not consented to messages until staff record it (consent is never inferred from the EHR).
2. MedicationStatement, AllergyIntolerance, Observation: set condition flags and eGFR. The
   sync only ever switches a flag on; clearing one is a person's decision. An older lab
   result never replaces a newer one.
3. Appointment: booked through the same service as a coordinator (approved protocols only,
   safety rule a), moved, cancelled or completed to match the EHR.

Each record is applied in its own savepoint, so one bad record is skipped and reported
(by field name, never by value) while the rest are saved. Cursors move only past types that
were read completely. Every change is audited as the system job "fhir_sync".
"""

import datetime as dt
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from prefix_api.db.models import (
    Appointment,
    Facility,
    IntegrationConnection,
    IntegrationSyncRun,
    Patient,
    ProcedureType,
)
from prefix_api.domain.enums import (
    AppointmentStatus,
    AuditAction,
    CancellationReason,
    SyncStatus,
)
from prefix_api.domain.errors import DomainError
from prefix_api.domain.fhir import (
    CREATININE_CODES,
    EGFR_CODES,
    LOINC,
    AppointmentRecord,
    FhirMappingError,
    KidneyResult,
    PatientRecord,
    Resource,
    age_on,
    ckd_epi_2021,
    is_contrast_allergy,
    kidney_result,
    last_updated,
    medication_flags,
    parse_appointment,
    parse_patient,
    reference_id,
)
from prefix_api.domain.principal import SystemPrincipal
from prefix_api.schemas.appointments import (
    AppointmentCancel,
    AppointmentCreate,
    AppointmentReschedule,
)
from prefix_api.services.appointments import AppointmentService
from prefix_api.services.audit import record_audit
from prefix_api.services.fhir.client import FhirClient, FhirError
from prefix_api.services.patients import apply_identifiers, erased_external_id, mrn_index
from prefix_api.services.timeline import cancel_events

JOB = "fhir_sync"
PURPOSE = "ehr_sync"
RESOURCE_TYPES = (
    "Patient",
    "MedicationStatement",
    "AllergyIntolerance",
    "Observation",
    "Appointment",
)
MAX_PROBLEMS = 25
_LIVE = (AppointmentStatus.SCHEDULED, AppointmentStatus.READY, AppointmentStatus.AT_RISK)

type ClientFactory = Callable[[IntegrationConnection], FhirClient]

# Only kidney results are read, not every lab result the EHR holds.
_SEARCH_FILTERS = {
    "Observation": {
        "code": ",".join(f"{LOINC}|{code}" for code in sorted(EGFR_CODES | CREATININE_CODES))
    },
}


class SkipRecordError(Exception):
    """This record cannot be applied. The message names the reason, never a value."""


@dataclass
class TypeCounts:
    read: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "read": self.read,
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
        }


@dataclass
class _State:
    counts: dict[str, TypeCounts] = field(default_factory=dict)
    problems: dict[str, int] = field(default_factory=dict)
    # Gender of patients read in this run, only to compute eGFR from creatinine.
    genders: dict[str, str | None] = field(default_factory=dict)


def _normalize_instant(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC).isoformat()


class FhirSync:
    def __init__(
        self,
        session: Session,
        connection: IntegrationConnection,
        client: FhirClient,
        *,
        now: dt.datetime,
    ) -> None:
        self._session = session
        self._conn = connection
        self._client = client
        self._now = now
        self._system = SystemPrincipal(organization_id=connection.organization_id, job=JOB)
        self._state = _State()

    # Run ------------------------------------------------------------------------------------

    def run(self) -> IntegrationSyncRun:
        conn = self._conn
        run = IntegrationSyncRun(
            organization_id=conn.organization_id,
            connection_id=conn.id,
            started_at=self._now,
            status=SyncStatus.RUNNING,
        )
        self._session.add(run)
        conn.last_sync_started_at = self._now
        self._session.flush()
        cursors = dict(conn.cursors or {})
        try:
            for resource_type in RESOURCE_TYPES:
                newest = self._sync_type(resource_type, cursors.get(resource_type))
                if newest is not None:
                    cursors[resource_type] = newest
                conn.cursors = dict(cursors)  # saved per type: a later failure keeps progress
                self._session.flush()
        except FhirError as exc:
            run.status = SyncStatus.FAILED
            run.error = str(exc)[:500]
            conn.last_error = run.error
            conn.consecutive_failures += 1
        else:
            run.status = SyncStatus.PARTIAL if self._state.problems else SyncStatus.SUCCEEDED
            conn.last_success_at = self._now
            conn.last_error = None
            conn.consecutive_failures = 0
        run.finished_at = self._now
        run.counts = {t: c.as_dict() for t, c in self._state.counts.items()}
        run.problems = [
            f"{reason} ({n})" if n > 1 else reason
            for reason, n in list(self._state.problems.items())[:MAX_PROBLEMS]
        ]
        self._session.flush()
        return run

    def _sync_type(self, resource_type: str, since: str | None) -> str | None:
        counts = self._state.counts.setdefault(resource_type, TypeCounts())
        apply = self._handlers()[resource_type]
        newest = since
        for resource in self._client.search(
            resource_type, since, _SEARCH_FILTERS.get(resource_type)
        ):
            counts.read += 1
            try:
                with self._session.begin_nested():
                    outcome = apply(resource)
            except (SkipRecordError, FhirMappingError, DomainError) as exc:
                counts.skipped += 1
                reason = f"{resource_type}: {exc}"
                self._state.problems[reason] = self._state.problems.get(reason, 0) + 1
            else:
                if outcome == "created":
                    counts.created += 1
                elif outcome == "updated":
                    counts.updated += 1
            stamp = _normalize_instant(last_updated(resource))
            if stamp is not None and (newest is None or stamp > newest):
                newest = stamp
        return newest

    def _handlers(self) -> dict[str, Callable[[Resource], str | None]]:
        return {
            "Patient": self._apply_patient,
            "MedicationStatement": self._apply_medication,
            "AllergyIntolerance": self._apply_allergy,
            "Observation": self._apply_observation,
            "Appointment": self._apply_appointment,
        }

    # Patients -------------------------------------------------------------------------------

    def _find_patient(self, fhir_id: str) -> Patient | None:
        org = self._conn.organization_id
        return self._session.scalar(
            select(Patient).where(
                Patient.organization_id == org,
                Patient.external_id.in_([fhir_id, erased_external_id(fhir_id)]),
            )
        )

    def _apply_patient(self, resource: Resource) -> str | None:
        record = parse_patient(resource)
        self._state.genders[record.fhir_id] = record.gender
        patient = self._find_patient(record.fhir_id)
        if patient is None and record.mrn:
            patient = self._session.scalar(
                select(Patient).where(
                    Patient.organization_id == self._conn.organization_id,
                    Patient.mrn_bidx == mrn_index(record.mrn),
                )
            )
        if patient is not None and patient.erased_at is not None:
            raise SkipRecordError("the patient's data was erased here, so it is not synced again")
        if patient is None:
            patient = Patient(
                organization_id=self._conn.organization_id,
                external_id=record.fhir_id,
                consent_to_message=False,
            )
            self._write_patient(patient, record)
            self._session.add(patient)
            self._session.flush()
            self._audit(AuditAction.CREATE, "patient", patient.id, patient.id)
            return "created"
        changed = self._write_patient(patient, record)
        if patient.external_id != record.fhir_id:
            patient.external_id = record.fhir_id
            changed.append("external_id")
        if not changed:
            return None
        self._session.flush()
        self._audit(AuditAction.UPDATE, "patient", patient.id, patient.id, fields=changed)
        return "updated"

    def _write_patient(self, patient: Patient, record: PatientRecord) -> list[str]:
        changed: list[str] = []
        new_values = {
            "first_name": record.first_name,
            "last_name": record.last_name,
            "date_of_birth": record.birth_date,
            "preferred_language": record.language,
        }
        for name, value in new_values.items():
            if getattr(patient, name, None) != value:
                setattr(patient, name, value)
                changed.append(name)
        current = (patient.phone, patient.email, patient.mrn)
        apply_identifiers(
            patient,
            phone=record.phone or patient.phone,
            email=record.email or patient.email,
            mrn=record.mrn or patient.mrn,
        )
        changed += [
            name
            for name, before, after in zip(
                ("phone", "email", "mrn"),
                current,
                (patient.phone, patient.email, patient.mrn),
                strict=True,
            )
            if before != after
        ]
        return changed

    def _subject(self, resource: Resource, *, field_name: str = "subject") -> Patient:
        ref = reference_id(resource.get(field_name) or resource.get("patient"), "Patient")
        if ref is None:
            raise SkipRecordError("no Patient reference")
        patient = self._find_patient(ref)
        if patient is None:
            raise SkipRecordError("its Patient is not synced")
        if patient.erased_at is not None:
            raise SkipRecordError("the patient's data was erased here")
        return patient

    # Conditions -----------------------------------------------------------------------------

    def _apply_medication(self, resource: Resource) -> str | None:
        flags = medication_flags(resource)
        if not flags:
            return None
        patient = self._subject(resource)
        changed = [flag.value for flag in sorted(flags) if not getattr(patient, flag.value)]
        for name in changed:
            setattr(patient, name, True)
        return self._flags_changed(patient, changed)

    def _apply_allergy(self, resource: Resource) -> str | None:
        if not is_contrast_allergy(resource):
            return None
        patient = self._subject(resource, field_name="patient")
        if patient.has_contrast_allergy:
            return None
        patient.has_contrast_allergy = True
        return self._flags_changed(patient, ["has_contrast_allergy"])

    def _apply_observation(self, resource: Resource) -> str | None:
        result = kidney_result(resource)
        if result is None:
            return None
        patient = self._subject(resource)
        egfr = self._egfr(result, patient)
        when = result.effective or self._now
        if patient.egfr_measured_at is not None and when <= patient.egfr_measured_at:
            return None
        patient.egfr_value = Decimal(str(round(egfr, 1)))
        patient.egfr_measured_at = when
        return self._flags_changed(patient, ["egfr_value"])

    def _egfr(self, result: KidneyResult, patient: Patient) -> float:
        if result.egfr is not None:
            return result.egfr
        if result.creatinine_mg_dl is None:
            raise SkipRecordError("no value")
        gender = self._gender(result.patient_ref)
        if gender is None:
            raise SkipRecordError(
                "creatinine without the patient's sex, so eGFR cannot be computed"
            )
        day = (result.effective or self._now).date()
        return ckd_epi_2021(result.creatinine_mg_dl, age_on(patient.date_of_birth, day), gender)

    def _gender(self, fhir_id: str) -> str | None:
        if fhir_id not in self._state.genders:
            self._state.genders[fhir_id] = parse_patient(
                self._client.read("Patient", fhir_id)
            ).gender
        return self._state.genders[fhir_id]

    def _flags_changed(self, patient: Patient, changed: list[str]) -> str | None:
        if not changed:
            return None
        self._session.flush()
        self._audit(AuditAction.UPDATE, "patient", patient.id, patient.id, fields=changed)
        return "updated"

    # Appointments ---------------------------------------------------------------------------

    def _apply_appointment(self, resource: Resource) -> str | None:
        record = parse_appointment(resource)
        patient = self._find_patient(record.patient_ref)
        if patient is None:
            # The appointment may be newer than our copy of the patient: fetch them once.
            self._apply_patient(self._client.read("Patient", record.patient_ref))
            patient = self._find_patient(record.patient_ref)
        if patient is None or patient.erased_at is not None:
            raise SkipRecordError("its Patient could not be synced")
        existing = self._session.scalar(
            select(Appointment).where(
                Appointment.organization_id == self._conn.organization_id,
                Appointment.external_id == record.fhir_id,
            )
        )
        if existing is None:
            return self._book(record, patient)
        return self._follow(existing, record)

    def _book(self, record: AppointmentRecord, patient: Patient) -> str | None:
        if record.status is not AppointmentStatus.SCHEDULED:
            return None  # cancelled or done before we ever saw it: nothing to prepare
        if record.start is None:
            raise SkipRecordError("Appointment.start is missing")
        if record.start <= self._now:
            return None  # already past
        appointment = AppointmentService(self._session).create(
            self._system,
            AppointmentCreate(
                patient_id=patient.id,
                facility_id=self._facility(record),
                procedure_type_id=self._procedure_type(record),
                scheduled_at=record.start,
                duration_minutes=record.duration_minutes,
            ),
            now=self._now,
        )
        appointment.external_id = record.fhir_id
        self._session.flush()
        return "created"

    def _follow(self, appointment: Appointment, record: AppointmentRecord) -> str | None:
        if appointment.status not in _LIVE:
            return None  # closed here; the EHR does not reopen it
        service = AppointmentService(self._session)
        if record.status is AppointmentStatus.CANCELLED:
            service.cancel(
                self._system,
                appointment.id,
                AppointmentCancel(
                    reason=record.cancellation_reason or CancellationReason.OTHER,
                    note="Cancelled in the hospital's EHR",
                ),
                now=self._now,
            )
            return "updated"
        if record.status is AppointmentStatus.COMPLETED:
            appointment.status = AppointmentStatus.COMPLETED
            appointment.completed_at = self._now
            cancel_events(self._session, appointment)
            self._session.flush()
            self._audit(
                AuditAction.UPDATE,
                "appointment",
                appointment.id,
                appointment.patient_id,
                fields=["status"],
            )
            return "updated"
        if record.start is not None and record.start != appointment.scheduled_at:
            if record.start <= self._now:
                raise SkipRecordError("moved into the past")
            service.reschedule(
                self._system,
                appointment.id,
                AppointmentReschedule(scheduled_at=record.start),
                now=self._now,
            )
            return "updated"
        return None

    def _procedure_type(self, record: AppointmentRecord) -> uuid.UUID:
        mapping: dict[str, str] = self._conn.mapping.get("service_codes", {})
        wanted = next((mapping[c] for c in record.service_codes if c in mapping), None)
        if wanted is None:
            raise SkipRecordError("no procedure type is mapped for its service code")
        procedure = self._session.scalar(
            select(ProcedureType.id).where(
                ProcedureType.organization_id == self._conn.organization_id,
                ProcedureType.code == wanted,
            )
        )
        if procedure is None:
            raise SkipRecordError("its mapped procedure type does not exist")
        return procedure

    def _facility(self, record: AppointmentRecord) -> uuid.UUID:
        locations: dict[str, str] = self._conn.mapping.get("locations", {})
        raw = locations.get(record.location_ref or "") or self._conn.mapping.get(
            "default_facility_id"
        )
        try:
            facility_id = uuid.UUID(str(raw))
        except ValueError as exc:
            raise SkipRecordError("no facility is mapped for its Location") from exc
        facility = self._session.get(Facility, facility_id)
        if facility is None or facility.organization_id != self._conn.organization_id:
            raise SkipRecordError("its mapped facility does not exist")
        return facility_id

    # Audit ----------------------------------------------------------------------------------

    def _audit(
        self,
        action: AuditAction,
        entity_type: str,
        entity_id: uuid.UUID,
        patient_id: uuid.UUID,
        *,
        fields: list[str] | None = None,
    ) -> None:
        record_audit(
            self._session,
            principal=self._system,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            patient_id=patient_id,
            purpose=PURPOSE,
            details={"fields": sorted(fields)} if fields else None,
        )


def iter_due_connections(session: Session) -> Iterator[IntegrationConnection]:
    yield from session.scalars(
        select(IntegrationConnection).where(
            IntegrationConnection.enabled.is_(True), IntegrationConnection.kind == "fhir"
        )
    )
