"""The integration admin API: FHIR set-up, health, sync now, and webhook endpoints."""

import datetime as dt

import pytest
from api_testkit import Builder, make_test_settings, staff_headers
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fhir_testkit import BASE_URL, Ehr, connect_ehr
from sqlalchemy.orm import Session, sessionmaker

from prefix_api.core.config import Settings
from prefix_api.domain.enums import Role
from prefix_api.main import create_app

NOW = dt.datetime.now(dt.UTC)


@pytest.fixture
def ehr(build: Builder, db_session: Session) -> Ehr:
    return connect_ehr(build, db_session, now=NOW)


@pytest.fixture
def wired(app: FastAPI, ehr: Ehr) -> Ehr:
    app.state.fhir_client_factory = ehr.client
    return ehr


def _admin(build: Builder, settings: Settings, ehr: Ehr) -> dict[str, str]:
    return staff_headers(settings, build.user(ehr.org, Role.ADMIN))


def test_only_admins_see_or_change_integrations(
    client: TestClient, build: Builder, settings: Settings, ehr: Ehr
) -> None:
    for role in (Role.COORDINATOR, Role.NURSE, Role.CLINICIAN, Role.VIEWER):
        headers = staff_headers(settings, build.user(ehr.org, role))
        assert client.get("/integrations/health", headers=headers).status_code == 403
        assert client.post("/integrations/fhir/sync", headers=headers).status_code == 403


def test_health_after_a_sync_run_now(
    client: TestClient, build: Builder, settings: Settings, wired: Ehr
) -> None:
    headers = _admin(build, settings, wired)
    before = client.get("/integrations/health", headers=headers).json()
    assert before["status"] == "never_run"

    run = client.post("/integrations/fhir/sync", headers=headers)

    assert run.status_code == 200, run.text
    assert run.json()["status"] == "PARTIAL"
    assert run.json()["counts"]["Patient"]["created"] == 2
    health = client.get("/integrations/health", headers=headers).json()
    assert health["status"] == "healthy"
    assert health["fhir"]["base_url"] == BASE_URL
    assert health["fhir"]["has_token"] is False
    assert set(health["fhir"]["cursors"]) >= {"Patient", "Appointment"}
    assert [r["status"] for r in health["runs"]] == ["PARTIAL"]


def test_set_up_checks_the_mapping_and_never_returns_the_token(
    client: TestClient, build: Builder, settings: Settings, ehr: Ehr
) -> None:
    headers = _admin(build, settings, ehr)
    token = "-".join(["bearer", "test", "value"])
    body: dict[str, object] = {
        "base_url": "https://fhir.city-hospital.example/r4",
        "token": token,
        "mapping": {
            "service_codes": {"73761001": "COLONOSCOPY"},
            "default_facility_id": str(ehr.facility.id),
        },
    }

    saved = client.put("/integrations/fhir", json=body, headers=headers)
    bad_code = client.put(
        "/integrations/fhir",
        json=body | {"mapping": {"service_codes": {"1": "BRAIN_SURGERY"}}},
        headers=headers,
    )

    assert saved.status_code == 200, saved.text
    assert saved.json()["fhir"]["has_token"] is True
    assert token not in saved.text
    assert saved.json()["fhir"]["cursors"] == {}  # a new server is read from the start
    assert bad_code.status_code == 422
    assert "BRAIN_SURGERY" in bad_code.json()["detail"]


def test_a_webhook_secret_is_shown_once(
    client: TestClient, build: Builder, settings: Settings, ehr: Ehr
) -> None:
    headers = _admin(build, settings, ehr)

    created = client.post(
        "/integrations/webhooks",
        json={"url": "https://ehr.example.test/hooks", "description": "EHR scheduling"},
        headers=headers,
    )

    assert created.status_code == 201
    secret = created.json()["secret"]
    assert secret.startswith("whsec_")
    health = client.get("/integrations/health", headers=headers)
    assert secret not in health.text
    [endpoint] = health.json()["webhooks"]
    assert endpoint["enabled"] is True
    off = client.patch(
        f"/integrations/webhooks/{endpoint['id']}", json={"enabled": False}, headers=headers
    )
    assert off.json()["enabled"] is False


def test_production_needs_https(
    session_factory: sessionmaker[Session], build: Builder, ehr: Ehr
) -> None:
    settings = make_test_settings(app_env="production")
    with TestClient(create_app(settings, session_factory=session_factory)) as client:
        headers = staff_headers(settings, build.user(ehr.org, Role.ADMIN))
        plain = client.post(
            "/integrations/webhooks", json={"url": "http://ehr.example.test/hooks"}, headers=headers
        )
        fhir = client.put(
            "/integrations/fhir", json={"base_url": "http://ehr.example.test/fhir"}, headers=headers
        )

    assert plain.status_code == 422
    assert fhir.status_code == 422
    assert "https" in plain.json()["detail"]
