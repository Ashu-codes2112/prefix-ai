"""Go-live compliance: consent history, export, erasure, retention and security incidents."""

import datetime as dt
import re
import zlib
from typing import Any

import pytest
from api_testkit import Builder, patient_headers, staff_headers
from fastapi.testclient import TestClient
from messaging_testkit import Booked, FakeTranslator, RecordingProvider, book
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from prefix_api.core.config import Settings
from prefix_api.core.crypto import get_blind_indexer, normalize_identifier
from prefix_api.db.models import (
    AuditLog,
    ConsentRecord,
    Message,
    Organization,
    Patient,
    PatientMagicLink,
)
from prefix_api.domain.enums import (
    AuditAction,
    ConsentSource,
    MessageChannel,
    MessageDirection,
    MessageStatus,
    Role,
    TimelineEventStatus,
)
from prefix_api.services.data_rights import ERASED, erasure_phrase
from prefix_api.services.dispatch import dispatch_due_reminders
from prefix_api.services.export_pdf import OTHER_SCRIPT, render_patient_export
from prefix_api.services.inbound import handle_inbound_message
from prefix_api.services.retention import purge_all

Factory = sessionmaker[Session]


def _admin(build: Builder, settings: Settings, booked: Booked) -> dict[str, str]:
    org = booked.patient.organization_id
    org_row = build.session.get_one(Organization, org)
    return staff_headers(settings, build.user(org_row, Role.ADMIN))


def _staff(build: Builder, settings: Settings, booked: Booked, role: Role) -> dict[str, str]:
    org_row = build.session.get_one(Organization, booked.patient.organization_id)
    return staff_headers(settings, build.user(org_row, role))


def _add_message(session: Session, booked: Booked, body: str) -> None:
    session.add(
        Message(
            organization_id=booked.patient.organization_id,
            patient_id=booked.patient.id,
            appointment_id=booked.appointment.id,
            channel=MessageChannel.SMS,
            direction=MessageDirection.INBOUND,
            status=MessageStatus.RECEIVED,
            language="en",
            body=body,
        )
    )
    session.flush()


# Consent ------------------------------------------------------------------------------------


def test_withdrawing_consent_is_kept_as_history_and_stops_reminders(
    client: TestClient,
    build: Builder,
    db_session: Session,
    settings: Settings,
    session_factory: Factory,
) -> None:
    booked = book(build, db_session, consent=False)
    headers = _staff(build, settings, booked, Role.COORDINATOR)
    pid = booked.patient.id

    assert (
        client.post(f"/patients/{pid}/consent", json={"granted": True}, headers=headers).status_code
        == 201
    )
    assert (
        client.post(
            f"/patients/{pid}/consent", json={"granted": False}, headers=headers
        ).status_code
        == 201
    )

    status = client.get(f"/patients/{pid}/consent", headers=headers).json()
    assert status["consent_to_message"] is False
    assert status["erased"] is False
    history = status["history"]
    assert [(h["granted"], h["source"], h["recorded_by_staff"]) for h in history] == [
        (False, "STAFF", True),
        (True, "STAFF", True),
    ]
    provider = RecordingProvider()
    event = booked.events["Low-fibre diet"]
    dispatch_due_reminders(
        session_factory,
        provider,
        FakeTranslator(),
        now=event.send_at + dt.timedelta(seconds=1),
        batch_size=50,
    )
    db_session.expire_all()
    assert provider.sent == []
    assert db_session.get_one(type(event), event.id).status is TimelineEventStatus.SKIPPED


def test_registering_with_consent_starts_the_history(
    client: TestClient, build: Builder, db_session: Session, settings: Settings
) -> None:
    org = build.organization()
    headers = staff_headers(settings, build.user(org, Role.COORDINATOR))
    created = client.post(
        "/patients",
        json={
            "first_name": "Ravi",
            "last_name": "Kumar",
            "date_of_birth": "1980-02-03",
            "phone": "+919811122233",
            "consent_to_message": True,
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text

    records = db_session.scalars(
        select(ConsentRecord).where(ConsentRecord.patient_id == created.json()["id"])
    ).all()
    assert [(r.granted, r.source) for r in records] == [(True, ConsentSource.STAFF)]


def test_a_stop_reply_withdraws_consent(build: Builder, db_session: Session) -> None:
    booked = book(build, db_session, consent=True)

    handle_inbound_message(
        db_session,
        from_phone=booked.patient.phone or "",
        body="STOP",
        channel=MessageChannel.SMS,
        provider="twilio",
        provider_message_id="SM1",
        now=dt.datetime.now(dt.UTC),
    )

    db_session.refresh(booked.patient)
    assert booked.patient.consent_to_message is False
    record = db_session.scalars(
        select(ConsentRecord).where(ConsentRecord.patient_id == booked.patient.id)
    ).one()
    assert (record.granted, record.source) == (False, ConsentSource.SMS_KEYWORD)


@pytest.mark.parametrize("reply", ["stop", "STOP!", "बंद करो", "band karo", "Unsubscribe"])
def test_stop_words_in_english_and_hindi(reply: str) -> None:
    from prefix_api.domain.replies import is_opt_out

    assert is_opt_out(reply)


@pytest.mark.parametrize("reply", ["cancel", "stop the bleeding?", "yes", "please don't stop"])
def test_other_replies_do_not_withdraw_consent(reply: str) -> None:
    from prefix_api.domain.replies import is_opt_out

    assert not is_opt_out(reply)


def test_consent_history_is_append_only(build: Builder, db_session: Session) -> None:
    booked = book(build, db_session, consent=True)
    record = ConsentRecord(
        organization_id=booked.patient.organization_id,
        patient_id=booked.patient.id,
        purpose="MESSAGING",
        granted=True,
        source=ConsentSource.STAFF,
        recorded_at=dt.datetime.now(dt.UTC),
    )
    db_session.add(record)
    db_session.flush()

    for statement in (
        "UPDATE consent_record SET granted = false WHERE id = :id",
        "DELETE FROM consent_record WHERE id = :id",
    ):
        with pytest.raises(DBAPIError, match="append-only"), db_session.begin_nested():
            db_session.execute(text(statement), {"id": record.id})


# Export -------------------------------------------------------------------------------------


def test_only_admins_export_and_erase(
    client: TestClient, build: Builder, db_session: Session, settings: Settings
) -> None:
    booked = book(build, db_session)
    pid = booked.patient.id
    for role in (Role.COORDINATOR, Role.NURSE, Role.CLINICIAN):
        headers = _staff(build, settings, booked, role)
        assert client.get(f"/patients/{pid}/export", headers=headers).status_code == 403
        erase = client.post(
            f"/patients/{pid}/erasure",
            json={"acknowledged": True, "phrase": erasure_phrase(pid)},
            headers=headers,
        )
        assert erase.status_code == 403


def test_the_json_export_holds_the_patients_data_and_is_audited(
    client: TestClient, build: Builder, db_session: Session, settings: Settings
) -> None:
    booked = book(build, db_session)
    _add_message(db_session, booked, "Can I have tea before?")
    headers = _admin(build, settings, booked)

    response = client.get(f"/patients/{booked.patient.id}/export", headers=headers)

    assert response.status_code == 200
    assert response.headers["content-disposition"].endswith('.json"')
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["patient"]["first_name"] == "Asha"
    assert data["patient"]["phone"] == booked.patient.phone
    assert [m["body"] for m in data["messages"]] == ["Can I have tea before?"]
    assert {e["step"] for e in data["prep_timeline"]} == set(booked.events)
    assert data["appointments"][0]["status"] == booked.appointment.status.value
    audit = db_session.scalars(
        select(AuditLog).where(
            AuditLog.patient_id == booked.patient.id, AuditLog.action == AuditAction.EXPORT
        )
    ).one()
    assert audit.entity_type == "patient_export"
    assert audit.details["status"] == "json"


def test_the_pdf_export_is_a_pdf(
    client: TestClient, build: Builder, db_session: Session, settings: Settings
) -> None:
    booked = book(build, db_session)
    headers = _admin(build, settings, booked)

    response = client.get(f"/patients/{booked.patient.id}/export?format=pdf", headers=headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    body = response.content
    assert body.startswith(b"%PDF-1.4")
    assert body.rstrip().endswith(b"%%EOF")
    text_ = _pdf_text(body)
    assert "Personal data held by" in text_
    assert "First name: Asha" in text_


def _pdf_text(pdf: bytes) -> str:
    chunks = re.findall(rb"stream\n(.*?)\nendstream", pdf, flags=re.S)
    return "\n".join(zlib.decompress(c).decode("cp1252") for c in chunks)


def test_the_pdf_points_to_the_json_for_other_scripts() -> None:
    data: dict[str, Any] = {
        "export": {"organization": "City Hospital", "generated_at": "now", "format_version": 1},
        "patient": {"first_name": "आशा", "last_name": "Rao (Dr)"},
        "messages": [{"body": "x" * 450}],
    }

    text_ = _pdf_text(render_patient_export(data))

    assert f"First name: {OTHER_SCRIPT}" in text_
    assert "Rao \\(Dr\\)" in text_  # parentheses are escaped in PDF strings
    assert "x" * 96 in text_  # long text wraps instead of running off the page


# Erasure ------------------------------------------------------------------------------------


def test_erasure_needs_both_confirmations(
    client: TestClient, build: Builder, db_session: Session, settings: Settings
) -> None:
    booked = book(build, db_session)
    headers = _admin(build, settings, booked)
    pid = booked.patient.id
    phrase = client.get(f"/patients/{pid}/erasure", headers=headers).json()["phrase"]
    assert phrase == erasure_phrase(pid)

    unticked = client.post(
        f"/patients/{pid}/erasure", json={"acknowledged": False, "phrase": phrase}, headers=headers
    )
    wrong = client.post(
        f"/patients/{pid}/erasure", json={"acknowledged": True, "phrase": "ERASE"}, headers=headers
    )

    assert unticked.status_code == 422
    assert wrong.status_code == 422
    assert phrase in wrong.json()["detail"]
    db_session.refresh(booked.patient)
    assert booked.patient.erased_at is None


def test_erasure_waits_until_upcoming_procedures_are_dealt_with(
    client: TestClient, build: Builder, db_session: Session, settings: Settings
) -> None:
    booked = book(build, db_session)
    headers = _admin(build, settings, booked)
    pid = booked.patient.id

    response = client.post(
        f"/patients/{pid}/erasure",
        json={"acknowledged": True, "phrase": erasure_phrase(pid)},
        headers=headers,
    )

    assert response.status_code == 422
    assert "upcoming procedures" in response.json()["detail"]


def test_erasure_removes_identifiers_and_free_text_but_keeps_a_deidentified_trail(
    client: TestClient, build: Builder, db_session: Session, settings: Settings
) -> None:
    booked = book(build, db_session, consent=True)
    _age(db_session, booked, 30)
    _add_message(db_session, booked, "My neighbour Sunita will bring me")
    portal = patient_headers(settings, booked.patient)
    assert client.get("/patient/me", headers=portal).status_code == 200
    headers = _admin(build, settings, booked)
    pid = booked.patient.id
    client.post(f"/patients/{pid}/magic-links", headers=headers)
    original_phone = booked.patient.phone

    response = client.post(
        f"/patients/{pid}/erasure",
        json={"acknowledged": True, "phrase": erasure_phrase(pid).lower()},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    db_session.expire_all()
    patient = db_session.get_one(Patient, pid)
    assert (patient.first_name, patient.last_name) == (ERASED, ERASED)
    assert patient.phone is None
    assert patient.email is None
    assert patient.mrn is None
    assert patient.phone_bidx is None
    assert patient.email_bidx is None
    assert patient.consent_to_message is False
    assert patient.erased_at is not None
    bodies = db_session.scalars(select(Message.body).where(Message.patient_id == pid)).all()
    assert bodies
    assert set(bodies) == {ERASED}
    assert (
        db_session.scalar(select(PatientMagicLink.id).where(PatientMagicLink.patient_id == pid))
        is None
    )
    # The appointment and its outcome stay for analytics.
    assert db_session.get(type(booked.appointment), booked.appointment.id) is not None
    # Consent history records the withdrawal; the audit trail names no one.
    sources = db_session.scalars(
        select(ConsentRecord.source).where(ConsentRecord.patient_id == pid)
    ).all()
    assert ConsentSource.ERASURE in sources
    erasure = db_session.scalars(
        select(AuditLog).where(
            AuditLog.entity_type == "patient_erasure", AuditLog.patient_id == pid
        )
    ).one()
    assert erasure.action is AuditAction.DELETE
    trail = " ".join(
        str(d)
        for d in db_session.scalars(select(AuditLog.details).where(AuditLog.patient_id == pid))
    )
    assert "Asha" not in trail
    assert (original_phone or "x") not in trail
    # Sessions and links stop working, and erasure cannot run twice.
    assert client.get("/patient/me", headers=portal).status_code == 401
    assert client.post(f"/patients/{pid}/magic-links", headers=headers).status_code == 422
    again = client.post(
        f"/patients/{pid}/erasure",
        json={"acknowledged": True, "phrase": erasure_phrase(pid)},
        headers=headers,
    )
    assert again.status_code == 422


# Retention ----------------------------------------------------------------------------------


def _age(session: Session, booked: Booked, days: int) -> None:
    """Move the patient's procedure (and registration) ``days`` into the past."""
    past = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    booked.appointment.scheduled_at = past
    booked.patient.created_at = past
    session.flush()


def test_retention_dry_run_purge_and_holds(
    client: TestClient, build: Builder, db_session: Session, settings: Settings
) -> None:
    old = book(build, db_session)
    org = db_session.get_one(Organization, old.patient.organization_id)
    headers = staff_headers(settings, build.user(org, Role.ADMIN))
    _age(db_session, old, 400)

    report = client.get("/compliance/retention", headers=headers).json()
    assert report["retention_days"] is None
    assert report["due"] == 0

    assert (
        client.put(
            "/compliance/retention", json={"retention_days": 10}, headers=headers
        ).status_code
        == 422
    )
    report = client.put(
        "/compliance/retention", json={"retention_days": 365}, headers=headers
    ).json()
    assert (report["dry_run"], report["due"], report["erased"]) == (True, 1, 0)
    db_session.refresh(old.patient)
    assert old.patient.erased_at is None  # a dry run changes nothing

    # An open incident that lists the patient holds the record.
    incident = client.post(
        "/compliance/incidents",
        json={
            "title": "Lost laptop",
            "description": "A laptop with an export was lost.",
            "severity": "HIGH",
            "discovered_at": dt.datetime.now(dt.UTC).isoformat(),
            "data_categories": ["contact"],
        },
        headers=headers,
    ).json()
    mrn = "MRN-OLD-1"
    old.patient.mrn = mrn
    old.patient.mrn_bidx = get_blind_indexer().index(normalize_identifier(mrn), "mrn")
    db_session.flush()
    client.post(
        f"/compliance/incidents/{incident['id']}/patients", json={"mrns": [mrn]}, headers=headers
    )
    report = client.get("/compliance/retention", headers=headers).json()
    assert (report["due"], report["held_for_incident"]) == (0, 1)

    client.post(
        f"/compliance/incidents/{incident['id']}/steps", json={"step": "closed"}, headers=headers
    )
    db_session.expire_all()  # the policy and incident were changed through the API
    erased = purge_all(db_session, now=dt.datetime.now(dt.UTC))
    assert erased == {org.id: 1}
    purged = db_session.get_one(Patient, old.patient.id)
    assert purged.erased_at is not None
    assert purged.first_name == ERASED


def test_retention_keeps_patients_with_upcoming_care(
    client: TestClient, build: Builder, db_session: Session, settings: Settings
) -> None:
    upcoming = book(build, db_session, days_ahead=5)
    upcoming.patient.created_at = dt.datetime.now(dt.UTC) - dt.timedelta(days=800)
    org = db_session.get_one(Organization, upcoming.patient.organization_id)
    org.retention_days = 365
    db_session.flush()
    headers = staff_headers(settings, build.user(org, Role.ADMIN))

    report = client.get("/compliance/retention", headers=headers).json()

    assert report["due"] == 0


def test_the_demo_hospital_is_never_purged(build: Builder, db_session: Session) -> None:
    old = book(build, db_session)
    org = db_session.get_one(Organization, old.patient.organization_id)
    org.is_demo = True
    org.retention_days = 30
    _age(db_session, old, 400)

    assert purge_all(db_session, now=dt.datetime.now(dt.UTC)) == {}


def test_only_admins_manage_retention_and_incidents(
    client: TestClient, build: Builder, db_session: Session, settings: Settings
) -> None:
    booked = book(build, db_session)
    headers = _staff(build, settings, booked, Role.COORDINATOR)

    assert client.get("/compliance/retention", headers=headers).status_code == 403
    assert client.get("/compliance/incidents", headers=headers).status_code == 403


# Incidents ----------------------------------------------------------------------------------


def test_an_incident_runs_from_report_to_close_and_exports_counts_only(
    client: TestClient, build: Builder, db_session: Session, settings: Settings
) -> None:
    booked = book(build, db_session)
    mrn = "MRN-42"
    booked.patient.mrn = mrn
    booked.patient.mrn_bidx = get_blind_indexer().index(normalize_identifier(mrn), "mrn")
    db_session.flush()
    headers = _admin(build, settings, booked)
    discovered = dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)

    created = client.post(
        "/compliance/incidents",
        json={
            "title": "List e-mailed to the wrong address",
            "description": "A procedure list went to an outside address.",
            "severity": "MEDIUM",
            "discovered_at": discovered.isoformat(),
            "data_categories": ["identity", "contact"],
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    incident = created.json()
    assert dt.datetime.fromisoformat(incident["notify_by"]) == discovered + dt.timedelta(hours=72)

    added = client.post(
        f"/compliance/incidents/{incident['id']}/patients",
        json={"mrns": [mrn, mrn.lower(), "NOPE-1"]},
        headers=headers,
    ).json()
    assert added == {"added": 1, "already_listed": 0, "not_found": 1}
    for step in ("contained", "authority_notified", "patients_notified"):
        r = client.post(
            f"/compliance/incidents/{incident['id']}/steps", json={"step": step}, headers=headers
        )
        assert r.status_code == 200, r.text
    assert r.json()["status"] == "NOTIFIED"
    assert r.json()["affected_patients"] == 1

    report = client.get(f"/compliance/incidents/{incident['id']}/report", headers=headers)
    assert report.status_code == 200
    data = report.json()
    assert data["affected_patients"] == 1
    assert data["timeline"]["notified_within_72_hours"] is True
    assert mrn not in report.text
    assert "Asha" not in report.text
    assert str(booked.patient.id) not in report.text

    client.post(
        f"/compliance/incidents/{incident['id']}/steps", json={"step": "closed"}, headers=headers
    )
    after = client.post(
        f"/compliance/incidents/{incident['id']}/steps", json={"step": "contained"}, headers=headers
    )
    assert after.status_code == 422
    listed = client.get("/compliance/incidents", headers=headers).json()
    assert [i["status"] for i in listed] == ["CLOSED"]


def test_incident_input_is_checked(
    client: TestClient, build: Builder, db_session: Session, settings: Settings
) -> None:
    booked = book(build, db_session)
    headers = _admin(build, settings, booked)
    base = {
        "title": "Something",
        "description": "Details",
        "severity": "LOW",
        "discovered_at": dt.datetime.now(dt.UTC).isoformat(),
    }

    future = client.post(
        "/compliance/incidents",
        json=base | {"discovered_at": (dt.datetime.now(dt.UTC) + dt.timedelta(days=1)).isoformat()},
        headers=headers,
    )
    unknown = client.post(
        "/compliance/incidents", json=base | {"data_categories": ["dna"]}, headers=headers
    )

    assert future.status_code == 422
    assert unknown.status_code == 422
    assert "dna" in unknown.json()["detail"]
