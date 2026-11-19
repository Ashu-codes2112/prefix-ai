"""A hospital wired to the FHIR fixture server, for the sync, webhook and API tests."""

import datetime as dt
from dataclasses import dataclass, field

import httpx2
from api_testkit import Builder
from messaging_testkit import STEPS
from sqlalchemy.orm import Session

from prefix_api.db.models import Facility, IntegrationConnection, Organization
from prefix_api.services.fhir.client import FhirClient
from prefix_api.services.fhir.fixture_server import FixtureFhirServer

BASE_URL = "https://ehr.example.test/fhir"


@dataclass
class Ehr:
    org: Organization
    facility: Facility
    connection: IntegrationConnection
    server: FixtureFhirServer
    sleeps: list[float] = field(default_factory=list)

    def client(self, _connection: IntegrationConnection | None = None) -> FhirClient:
        return FhirClient(
            BASE_URL,
            client=httpx2.Client(transport=self.server.transport()),
            backoff_seconds=0.01,
            sleep=self.sleeps.append,
        )


def connect_ehr(build: Builder, session: Session, *, now: dt.datetime) -> Ehr:
    org = build.organization("City Hospital")
    facility = build.facility(org)
    procedure = build.procedure_type(org, "COLONOSCOPY")
    build.approved_protocol(org, procedure, STEPS)
    connection = IntegrationConnection(
        organization_id=org.id,
        kind="fhir",
        base_url=BASE_URL,
        enabled=True,
        mapping={
            "service_codes": {"73761001": "COLONOSCOPY"},
            "locations": {"loc-endo": str(facility.id)},
            "default_facility_id": str(facility.id),
        },
        cursors={},
    )
    session.add(connection)
    session.flush()
    return Ehr(org=org, facility=facility, connection=connection, server=FixtureFhirServer(now=now))
