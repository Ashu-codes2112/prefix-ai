"""Outbound webhooks: an outbox for appointment status changes, signed and retried."""

import datetime as dt
import json

import httpx2
import pytest
from api_testkit import Builder
from messaging_testkit import Booked, book
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from prefix_api.db.models import Organization, WebhookDelivery, WebhookEndpoint
from prefix_api.domain.enums import AppointmentStatus, CancellationReason, DeliveryStatus
from prefix_api.domain.principal import SystemPrincipal
from prefix_api.schemas.appointments import AppointmentCancel
from prefix_api.services.appointments import AppointmentService
from prefix_api.services.webhooks import (
    BACKOFF,
    EVENT_STATUS_CHANGED,
    MAX_ATTEMPTS,
    deliver_due,
    signature,
    verify_signature,
)

SECRET = "-".join(["whsec", "test", "value"])
NOW = dt.datetime.now(dt.UTC)


def _endpoint(session: Session, booked: Booked, *, enabled: bool = True) -> WebhookEndpoint:
    endpoint = WebhookEndpoint(
        organization_id=booked.patient.organization_id,
        url="https://ehr.example.test/hooks/prefix",
        secret=SECRET,
        enabled=enabled,
    )
    session.add(endpoint)
    session.flush()
    return endpoint


def _cancel(session: Session, booked: Booked) -> None:
    AppointmentService(session).cancel(
        SystemPrincipal(organization_id=booked.patient.organization_id, job="test"),
        booked.appointment.id,
        AppointmentCancel(reason=CancellationReason.NO_ESCORT, note="No one can drive her home"),
        now=NOW,
    )
    session.flush()


def _deliveries(session: Session, booked: Booked) -> list[WebhookDelivery]:
    return list(
        session.scalars(
            select(WebhookDelivery).where(
                WebhookDelivery.organization_id == booked.patient.organization_id
            )
        )
    )


def test_a_status_change_writes_one_delivery_per_endpoint_with_no_patient_data(
    build: Builder, db_session: Session
) -> None:
    booked = book(build, db_session)
    booked.appointment.external_id = "appt-77"
    _endpoint(db_session, booked)
    _endpoint(db_session, booked, enabled=False)

    _cancel(db_session, booked)

    [delivery] = _deliveries(db_session, booked)
    assert delivery.event == EVENT_STATUS_CHANGED
    assert delivery.status is DeliveryStatus.PENDING
    data = delivery.payload["data"]
    assert data == {
        "appointment_id": str(booked.appointment.id),
        "external_id": "appt-77",
        "status": "CANCELLED",
        "previous_status": "SCHEDULED",
        "scheduled_at": booked.appointment.scheduled_at.isoformat(),
        "cancellation_reason": "NO_ESCORT",
    }
    text = json.dumps(delivery.payload)
    assert booked.patient.first_name not in text
    assert "drive her home" not in text
    assert (booked.patient.phone or "x") not in text


def test_no_change_no_delivery_and_the_demo_hospital_never_sends(
    build: Builder, db_session: Session
) -> None:
    booked = book(build, db_session)
    _endpoint(db_session, booked)
    booked.appointment.duration_minutes = 60  # not a status change
    db_session.flush()
    assert _deliveries(db_session, booked) == []

    org = db_session.get_one(Organization, booked.patient.organization_id)
    org.is_demo = True
    booked.appointment.status = AppointmentStatus.AT_RISK
    db_session.flush()

    assert _deliveries(db_session, booked) == []


def test_signatures_verify_and_reject_tampering_or_old_stamps() -> None:
    body = b'{"type":"appointment.status_changed"}'
    header = signature(SECRET, 1_800_000_000, body)

    assert header.startswith("t=1800000000,v1=")
    assert verify_signature(SECRET, header, body, now=1_800_000_100)
    assert not verify_signature(SECRET, header, body + b" ", now=1_800_000_100)
    assert not verify_signature("other", header, body, now=1_800_000_100)
    assert not verify_signature(SECRET, header, body, now=1_800_000_000 + 301)
    assert not verify_signature(SECRET, "nonsense", body, now=1_800_000_000)


def _client(statuses: list[int], seen: list[httpx2.Request]) -> httpx2.Client:
    def handle(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        status = statuses.pop(0) if statuses else 200
        if status == 0:
            raise httpx2.ConnectError("refused")
        return httpx2.Response(status)

    return httpx2.Client(transport=httpx2.MockTransport(handle))


def test_the_worker_posts_signed_deliveries_once(
    build: Builder, db_session: Session, session_factory: sessionmaker[Session]
) -> None:
    booked = book(build, db_session)
    endpoint = _endpoint(db_session, booked)
    _cancel(db_session, booked)
    db_session.commit()
    seen: list[httpx2.Request] = []
    # The outbox stamps rows with the real time; deliver a little after it.
    at = _deliveries(db_session, booked)[0].next_attempt_at + dt.timedelta(seconds=5)

    result = deliver_due(session_factory, now=at, client=_client([200], seen))

    assert (result.delivered, result.retrying, result.failed) == (1, 0, 0)
    [request] = seen
    assert str(request.url) == endpoint.url
    [delivery] = _deliveries(db_session, booked)
    db_session.refresh(delivery)
    assert delivery.status is DeliveryStatus.DELIVERED
    assert request.headers["X-Prefix-Delivery"] == str(delivery.id)
    assert request.headers["X-Prefix-Event"] == EVENT_STATUS_CHANGED
    assert verify_signature(
        SECRET,
        request.headers["X-Prefix-Signature"],
        request.content,
        now=int(at.timestamp()),
    )
    # Delivered rows are not sent again.
    again = deliver_due(session_factory, now=at + dt.timedelta(minutes=5), client=_client([], seen))
    assert again.delivered == 0
    assert len(seen) == 1


@pytest.mark.parametrize("first_failure", [500, 0], ids=["server error", "network error"])
def test_failures_back_off_and_finally_give_up(
    build: Builder,
    db_session: Session,
    session_factory: sessionmaker[Session],
    first_failure: int,
) -> None:
    booked = book(build, db_session)
    _endpoint(db_session, booked)
    _cancel(db_session, booked)
    db_session.commit()
    seen: list[httpx2.Request] = []

    at = _deliveries(db_session, booked)[0].next_attempt_at + dt.timedelta(seconds=5)
    first = deliver_due(session_factory, now=at, client=_client([first_failure], seen))
    [delivery] = _deliveries(db_session, booked)
    db_session.refresh(delivery)
    assert first.retrying == 1
    assert delivery.next_attempt_at == at + BACKOFF[0]
    assert delivery.last_error in ("HTTP 500", "ConnectError")
    assert (
        deliver_due(
            session_factory, now=at + dt.timedelta(seconds=30), client=_client([], seen)
        ).retrying
        == 0
    )

    for _ in range(MAX_ATTEMPTS - 1):
        db_session.refresh(delivery)
        deliver_due(session_factory, now=delivery.next_attempt_at, client=_client([503], seen))
    db_session.refresh(delivery)
    assert delivery.status is DeliveryStatus.FAILED
    assert delivery.attempts == MAX_ATTEMPTS
