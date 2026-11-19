"""Messaging status for the staff app, and the admin-only test message: demo mode, the Demo
Hospital exception, the rate limit and the audit entry."""

from collections.abc import Iterator

import pytest
from api_testkit import Builder, make_test_settings, staff_headers
from fastapi.testclient import TestClient
from messaging_testkit import RecordingProvider
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from prefix_api.db.models import AuditLog
from prefix_api.domain.enums import AuditAction, MessageChannel, Role
from prefix_api.main import create_app
from prefix_api.services.messaging_status import TESTS_PER_WINDOW

LIVE = {
    "twilio_account_sid": "AC1",
    "twilio_auth_token": "tok",
    "twilio_from_number": "+15550001111",
}
PHONE = "+919800000123"


@pytest.fixture
def provider() -> RecordingProvider:
    return RecordingProvider()


@pytest.fixture
def live_client(
    session_factory: sessionmaker[Session], provider: RecordingProvider
) -> Iterator[TestClient]:
    app = create_app(make_test_settings(**LIVE), session_factory=session_factory)
    with TestClient(app) as client:
        app.state.messaging_provider = provider
        yield client


def demo_admin(build: Builder, db_session: Session, role: Role = Role.ADMIN) -> dict[str, str]:
    org = build.organization("Demo Hospital")
    org.is_demo = True
    db_session.flush()
    return staff_headers(make_test_settings(), build.user(org, role))


def test_without_twilio_the_staff_app_is_told_it_is_demo_mode(
    client: TestClient, build: Builder, db_session: Session
) -> None:
    coordinator = staff_headers(
        make_test_settings(), build.user(build.organization(), Role.COORDINATOR)
    )

    status = client.get("/messaging/status", headers=coordinator).json()

    assert status == {
        "mode": "demo",
        "sms_sender": None,
        "whatsapp_sender": None,
        "demo_hospital": False,
        "can_send_test": False,
    }


def test_with_twilio_the_status_is_live_with_masked_senders(
    live_client: TestClient, build: Builder, db_session: Session
) -> None:
    status = live_client.get("/messaging/status", headers=demo_admin(build, db_session)).json()

    assert status["mode"] == "live"
    assert status["sms_sender"] == status["whatsapp_sender"] == "***111"
    assert status["demo_hospital"] is True
    assert status["can_send_test"] is True


def test_the_demo_admin_can_send_one_real_test_message(
    live_client: TestClient, build: Builder, db_session: Session, provider: RecordingProvider
) -> None:
    response = live_client.post(
        "/messaging/test",
        json={"phone": PHONE, "channel": "WHATSAPP"},
        headers=demo_admin(build, db_session),
    )

    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == "sent"
    assert response.json()["detail"] == "Sent to ***123 by WHATSAPP."
    [sent] = provider.sent
    assert (sent.to, sent.channel) == (PHONE, MessageChannel.WHATSAPP)
    assert "test message" in sent.body


def test_the_test_message_is_audited_without_the_number(
    live_client: TestClient, build: Builder, db_session: Session
) -> None:
    headers = demo_admin(build, db_session)

    live_client.post("/messaging/test", json={"phone": PHONE}, headers=headers)

    [entry] = db_session.scalars(
        select(AuditLog).where(AuditLog.entity_type == "messaging_test")
    ).all()
    assert entry.action is AuditAction.CREATE
    assert entry.purpose == "messaging_test"
    assert entry.details == {"status": "sent"}
    assert PHONE not in str(entry.details)


def test_in_demo_mode_the_test_message_is_only_logged(
    client: TestClient, build: Builder, db_session: Session
) -> None:
    response = client.post(
        "/messaging/test", json={"phone": PHONE}, headers=demo_admin(build, db_session)
    )

    assert response.json()["outcome"] == "logged"
    assert response.json()["mode"] == "demo"


@pytest.mark.parametrize("role", [Role.COORDINATOR, Role.NURSE, Role.DEMO_VIEWER])
def test_only_admins_may_send_a_test_message(
    live_client: TestClient,
    build: Builder,
    db_session: Session,
    provider: RecordingProvider,
    role: Role,
) -> None:
    response = live_client.post(
        "/messaging/test", json={"phone": PHONE}, headers=demo_admin(build, db_session, role)
    )

    assert response.status_code == 403
    assert provider.sent == []


def test_test_messages_are_rate_limited(
    live_client: TestClient, build: Builder, db_session: Session, provider: RecordingProvider
) -> None:
    headers = demo_admin(build, db_session)
    for _ in range(TESTS_PER_WINDOW):
        sent = live_client.post("/messaging/test", json={"phone": PHONE}, headers=headers)
        assert sent.status_code == 200

    response = live_client.post("/messaging/test", json={"phone": PHONE}, headers=headers)

    assert response.status_code == 429
    assert len(provider.sent) == TESTS_PER_WINDOW


def test_a_failed_test_message_says_why(
    live_client: TestClient, build: Builder, db_session: Session, provider: RecordingProvider
) -> None:
    provider.fail_with = "Twilio rejected the message (HTTP 400)"

    response = live_client.post(
        "/messaging/test", json={"phone": PHONE}, headers=demo_admin(build, db_session)
    )

    assert response.json() == {
        "outcome": "failed",
        "mode": "live",
        "detail": "Twilio rejected the message (HTTP 400)",
    }


def test_only_a_phone_number_is_accepted(
    live_client: TestClient, build: Builder, db_session: Session
) -> None:
    response = live_client.post(
        "/messaging/test", json={"phone": "not-a-number"}, headers=demo_admin(build, db_session)
    )

    assert response.status_code == 422
