"""Incremental FHIR R4 sync against recorded fixtures: patients, conditions and bookings."""

import datetime as dt
from decimal import Decimal

import pytest
from api_testkit import Builder
from fhir_testkit import Ehr, connect_ehr
from sqlalchemy import select
from sqlalchemy.orm import Session

from prefix_api.core.crypto import get_blind_indexer, normalize_identifier
from prefix_api.db.models import (
    Appointment,
    AuditLog,
    IntegrationSyncRun,
    Patient,
    PatientTimelineEvent,
)
from prefix_api.domain.enums import (
    ActorType,
    AppointmentStatus,
    CancellationReason,
    SyncStatus,
)
from prefix_api.domain.principal import SystemPrincipal
from prefix_api.services.data_rights import ERASED, erase_patient
from prefix_api.services.fhir.sync import FhirSync

NOW = dt.datetime.now(dt.UTC)


@pytest.fixture
def ehr(build: Builder, db_session: Session) -> Ehr:
    return connect_ehr(build, db_session, now=NOW)


def sync(db_session: Session, ehr: Ehr, *, at: dt.datetime = NOW) -> IntegrationSyncRun:
    return FhirSync(db_session, ehr.connection, ehr.client(), now=at).run()


def patient(db_session: Session, ehr: Ehr, fhir_id: str) -> Patient:
    return db_session.scalars(
        select(Patient).where(Patient.organization_id == ehr.org.id, Patient.external_id == fhir_id)
    ).one()


def booking(db_session: Session, ehr: Ehr, fhir_id: str) -> Appointment:
    return db_session.scalars(
        select(Appointment).where(
            Appointment.organization_id == ehr.org.id, Appointment.external_id == fhir_id
        )
    ).one()


def test_a_first_sync_brings_in_patients_conditions_and_bookings(
    db_session: Session, ehr: Ehr
) -> None:
    run = sync(db_session, ehr)

    assert run.status is SyncStatus.PARTIAL  # two records could not be used; see problems
    assert run.counts["Patient"] == {"read": 3, "created": 2, "updated": 0, "skipped": 1}
    assert run.counts["Appointment"]["created"] == 2
    assert run.counts["Appointment"]["skipped"] == 1
    assert sorted(run.problems) == [
        "Appointment: no procedure type is mapped for its service code",
        "Patient: Patient.name needs a family and a given name",
    ]

    kavya = patient(db_session, ehr, "pat-1001")
    assert (kavya.first_name, kavya.last_name, kavya.mrn) == ("Kavya", "Sharma", "FH-1001")
    assert kavya.preferred_language == "hi"
    assert kavya.consent_to_message is False  # consent is recorded by staff, never inferred
    assert kavya.on_anticoagulant is True
    assert kavya.has_contrast_allergy is True
    assert kavya.on_glp1_agonist is False
    # The newest result wins, even though the older one (75) was updated later.
    assert kavya.egfr_value == Decimal("52.0")

    rohan = patient(db_session, ehr, "pat-1002")
    assert rohan.diabetic_on_insulin is True
    assert rohan.on_glp1_agonist is True
    assert rohan.on_anticoagulant is False  # his warfarin is stopped
    assert rohan.has_contrast_allergy is False  # penicillin is not contrast
    assert rohan.egfr_value == Decimal("54.1")  # from creatinine 1.4 mg/dL, man aged 70

    first = booking(db_session, ehr, "appt-1")
    assert first.status is AppointmentStatus.SCHEDULED
    assert first.facility_id == ehr.facility.id
    assert first.duration_minutes == 45
    events = db_session.scalars(
        select(PatientTimelineEvent).where(PatientTimelineEvent.appointment_id == first.id)
    ).all()
    assert len(events) == 3  # the approved protocol's steps, as for a coordinator's booking

    audit = db_session.scalars(
        select(AuditLog).where(AuditLog.patient_id == kavya.id, AuditLog.purpose == "ehr_sync")
    ).all()
    assert audit
    assert {a.actor_type for a in audit} == {ActorType.SYSTEM}
    assert ehr.connection.consecutive_failures == 0
    assert ehr.connection.last_success_at == NOW


def test_the_next_sync_reads_only_what_changed(db_session: Session, ehr: Ehr) -> None:
    sync(db_session, ehr)
    cursors = dict(ehr.connection.cursors)
    assert set(cursors) == {
        "Patient",
        "MedicationStatement",
        "AllergyIntolerance",
        "Observation",
        "Appointment",
    }

    again = sync(db_session, ehr, at=NOW + dt.timedelta(minutes=15))

    assert again.status is SyncStatus.SUCCEEDED
    assert all(counts["read"] == 0 for counts in again.counts.values())
    assert any("_lastUpdated=gt" in url for url in ehr.server.requests[-5:])


def test_changes_in_the_ehr_move_and_cancel_bookings(db_session: Session, ehr: Ehr) -> None:
    sync(db_session, ehr)
    later = NOW + dt.timedelta(minutes=15)
    moved = ehr.server.get("Appointment", "appt-2")
    new_start = dt.datetime.fromisoformat(moved["start"]) + dt.timedelta(days=2)
    moved["start"] = new_start.isoformat()
    ehr.server.upsert(moved, at=later)
    cancelled = ehr.server.get("Appointment", "appt-1")
    cancelled["status"] = "cancelled"
    cancelled["cancelationReason"] = {"coding": [{"code": "pat"}]}
    ehr.server.upsert(cancelled, at=later)

    run = sync(db_session, ehr, at=later + dt.timedelta(seconds=1))

    assert run.counts["Appointment"] == {"read": 2, "created": 0, "updated": 2, "skipped": 0}
    first = booking(db_session, ehr, "appt-1")
    assert first.status is AppointmentStatus.CANCELLED
    assert first.cancellation_reason is CancellationReason.PATIENT_REQUESTED
    assert booking(db_session, ehr, "appt-2").scheduled_at == new_start


def test_retries_ride_out_a_busy_server(db_session: Session, ehr: Ehr) -> None:
    ehr.server.fail_next = [503, 429]

    run = sync(db_session, ehr)

    assert run.status is SyncStatus.PARTIAL
    assert run.error is None
    assert ehr.sleeps[0] == pytest.approx(0.01)  # backoff after the 503
    assert ehr.sleeps[1] == 1.0  # Retry-After from the 429


def test_a_server_that_stays_down_fails_the_run_and_keeps_the_cursors(
    db_session: Session, ehr: Ehr
) -> None:
    ehr.server.fail_next = [500] * 10

    run = sync(db_session, ehr)

    assert run.status is SyncStatus.FAILED
    assert run.error == "The FHIR server could not be reached (HTTP 500) after retries"
    assert ehr.connection.consecutive_failures == 1
    assert ehr.connection.last_error == run.error
    assert ehr.connection.cursors == {}
    assert ehr.connection.last_success_at is None


def test_a_refused_login_is_not_retried(db_session: Session, ehr: Ehr) -> None:
    ehr.server.fail_next = [401]

    run = sync(db_session, ehr)

    assert run.error == "The FHIR server answered HTTP 401"
    assert ehr.sleeps == []


def test_an_erased_patient_is_never_synced_back(db_session: Session, ehr: Ehr) -> None:
    sync(db_session, ehr)
    kavya = patient(db_session, ehr, "pat-1001")
    for appointment in db_session.scalars(
        select(Appointment).where(Appointment.patient_id == kavya.id)
    ):
        appointment.status = AppointmentStatus.CANCELLED
    erase_patient(
        db_session,
        kavya,
        principal=SystemPrincipal(organization_id=ehr.org.id, job="test"),
        now=NOW,
        reason="request",
    )
    later = NOW + dt.timedelta(minutes=15)
    ehr.server.upsert(ehr.server.get("Patient", "pat-1001"), at=later)

    run = sync(db_session, ehr, at=later + dt.timedelta(seconds=1))

    assert run.counts["Patient"]["skipped"] == 1
    db_session.refresh(kavya)
    assert kavya.first_name == ERASED
    assert kavya.external_id is not None
    assert kavya.external_id.startswith("erased:")
    names = db_session.scalars(
        select(Patient.last_name).where(Patient.organization_id == ehr.org.id)
    ).all()
    assert "Sharma" not in names


def test_the_sync_only_ever_switches_a_condition_on(db_session: Session, ehr: Ehr) -> None:
    sync(db_session, ehr)
    later = NOW + dt.timedelta(minutes=15)
    stopped = ehr.server.get("MedicationStatement", "med-1")
    stopped["status"] = "stopped"
    ehr.server.upsert(stopped, at=later)

    sync(db_session, ehr, at=later + dt.timedelta(seconds=1))

    assert patient(db_session, ehr, "pat-1001").on_anticoagulant is True


def test_a_patient_already_here_is_matched_by_mrn(
    build: Builder, db_session: Session, ehr: Ehr
) -> None:
    existing = build.patient(ehr.org, first_name="Rohan", last_name="Iyer")
    existing.mrn = "FH-1002"
    existing.mrn_bidx = get_blind_indexer().index(normalize_identifier("FH-1002"), "mrn")
    db_session.flush()

    run = sync(db_session, ehr)

    assert run.counts["Patient"]["updated"] == 1
    db_session.refresh(existing)
    assert existing.external_id == "pat-1002"
    assert existing.first_name == "Rohan K."
