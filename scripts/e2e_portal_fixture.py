"""Create patients with fresh portal links, for a demo or for the end-to-end tests.

By default the patients join the Demo Hospital, the same one the seeded admin and "Try as
Guest" use, so what they do in the portal reaches that dashboard:

    uv run python scripts/e2e_portal_fixture.py

prints {"en": "<link>", "hi": "<link>"}: an English- and a Hindi-speaking patient, each booked
on the hospital's next slots about two days out.

With --isolated (the Playwright tests), each run instead creates a fresh organization (so runs
never interfere) with an approved protocol, a patient per test with an appointment two days
out, and one magic link each. A second organization holds only the staff-notification test,
so other tests running in parallel cannot add to its bell. It prints
{"en": "<link>", "hi": "<link>", "reuse": "<link>", ..., "staff_email": ...}.

Either way the Demo Hospital is set up (and given its synthetic patients) if it is not yet.
Development and CI only: refuses to run against production.
"""

import argparse
import datetime as dt
import json
import secrets
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from prefix_api.core.config import Settings, get_settings
from prefix_api.core.crypto import configure_crypto
from prefix_api.core.security import hash_password
from prefix_api.db.models import Facility, Organization, Patient, ProcedureType, User
from prefix_api.db.session import create_db_engine, create_session_factory
from prefix_api.domain.enums import MessageChannel, Role, StepCategory
from prefix_api.domain.principal import StaffPrincipal
from prefix_api.schemas.appointments import AppointmentCreate
from prefix_api.schemas.patients import PatientCreate
from prefix_api.schemas.protocols import ProtocolIn, StepIn
from prefix_api.seed import DEMO_ADMIN_EMAIL, admin_password_from_env, ensure_demo_hospital
from prefix_api.services.appointments import AppointmentService
from prefix_api.services.demo import demo_protocol_id, demo_slot, populate_demo
from prefix_api.services.embeddings import get_embedding_provider
from prefix_api.services.patient_access import PatientAccessService
from prefix_api.services.patients import PatientService
from prefix_api.services.protocols import ProtocolService

DAY = 24 * 60
STEPS = [
    # Applies from 3 days before: "in effect" now.
    StepIn(
        offset_minutes=3 * DAY,
        title="Low-fibre diet",
        instruction_text="Eat low-fibre food until the day before your procedure.",
        category=StepCategory.DIET,
    ),
    # Due an hour ago: within the confirmation grace period, so "Do this now".
    StepIn(
        offset_minutes=2 * DAY,
        title="Arrange an escort",
        instruction_text="Ask an adult to take you home after the procedure.",
        category=StepCategory.LOGISTICS,
        requires_confirmation=True,
    ),
    # Tomorrow: "Coming up", and a problem can be reported already.
    StepIn(
        offset_minutes=1 * DAY,
        title="Clear liquids only",
        instruction_text="From now on, have clear liquids only.",
        category=StepCategory.DIET,
        requires_confirmation=True,
        is_critical=True,
    ),
]
STAFF_PASSWORD = (
    "E2E-Staff-Password-2026!"  # pragma: allowlist secret - test data only, never a real account
)
PATIENTS = {
    "en": ("Asha", "en"),
    "hi": ("Ravi", "hi"),
    "reuse": ("Meera", "en"),
    # Its own link: magic links are single-use, and the assistant test must not share one.
    "assist": ("Priya", "en"),
    # A care-team message is sent to this patient, then read in the portal's Updates.
    "message": ("Neha", "en"),
    # Accessibility scans of every portal tab, one patient per browser project.
    "a11y_desktop": ("Kavya", "en"),
    "a11y_mobile": ("Isha", "en"),
}
# In a hospital of its own: raises problems that must reach the top of the staff bell.
BELL_PATIENTS = {"bell": ("Sunita", "en")}
# For a demo: patients of the Demo Hospital.
DEMO_PATIENTS = {"en": ("Asha", "en"), "hi": ("Ravi", "hi")}


def _hospital(
    session: Session,
    settings: Settings,
    patients: dict[str, tuple[str, str]],
    now: dt.datetime,
) -> tuple[dict[str, str], str]:
    """One organization with a protocol, its patients' bookings and their magic links.

    Returns the links and the coordinator's email."""
    suffix = secrets.token_hex(4)
    org = Organization(
        name=f"E2E Hospital {suffix}",
        slug=f"e2e-{suffix}",
        country_code="IN",
        default_timezone="Asia/Kolkata",
    )
    session.add(org)
    session.flush()
    facility = Facility(
        organization_id=org.id,
        name="E2E Main Campus",
        timezone="Asia/Kolkata",
        address="1 Test Road, Bengaluru",
        maps_url="https://maps.example.com/e2e",
        phone="+911100000000",
    )
    procedure = ProcedureType(
        organization_id=org.id,
        code="COLONOSCOPY",
        name="Colonoscopy",
        default_duration_minutes=45,
        or_cost_per_minute=Decimal("150.00"),
        requires_sedation=True,
    )
    users = {
        role: User(
            organization_id=org.id,
            email=f"{role.value.lower()}-{suffix}@e2e.example.com",
            full_name=f"E2E {role.value.title()}",
            role=role,
            # Known only to this throwaway test organisation, so E2E tests can sign in.
            password_hash=hash_password(STAFF_PASSWORD),
        )
        for role in (Role.CLINICIAN, Role.COORDINATOR)
    }
    session.add_all([facility, procedure, *users.values()])
    session.flush()
    clinician, coordinator = (
        StaffPrincipal(user_id=u.id, organization_id=org.id, role=u.role) for u in users.values()
    )

    protocols = ProtocolService(session, get_embedding_provider("local"))
    protocol = protocols.create(
        clinician,
        ProtocolIn(procedure_type_id=procedure.id, name="E2E prep", steps=STEPS),
    )
    protocols.approve(clinician, protocol.id, acknowledge_sample=False, now=now)

    scheduled_at = now + dt.timedelta(days=2, hours=-1)
    links = {}
    for key, (first_name, language) in patients.items():
        patient = PatientService(session).create(
            coordinator,
            PatientCreate(
                first_name=first_name,
                last_name="Test",
                date_of_birth=dt.date(1980, 1, 1),
                preferred_language=language,
                preferred_channel=MessageChannel.PORTAL,
            ),
            now=now,
        )
        session.flush()
        AppointmentService(session).create(
            coordinator,
            AppointmentCreate(
                patient_id=patient.id,
                facility_id=facility.id,
                procedure_type_id=procedure.id,
                protocol_id=demo_protocol_id(session, org.id, procedure.id),
                scheduled_at=scheduled_at,
                arrival_at=scheduled_at - dt.timedelta(hours=1),
            ),
            now=now,
        )
        link = PatientAccessService(session, settings).issue_magic_link(
            coordinator, patient.id, now=now
        )
        links[key] = link.url
    return links, users[Role.COORDINATOR].email


def _ensure_demo(factory: sessionmaker[Session]) -> None:
    """The Demo Hospital, with its synthetic patients if it has none yet (CI starts empty)."""
    with factory() as session:
        hospital = ensure_demo_hospital(session, admin_password=admin_password_from_env())
        org_id = hospital.organization.id
        empty = (
            session.scalar(select(Patient.id).where(Patient.organization_id == org_id).limit(1))
            is None
        )
        session.commit()
    if empty:
        populate_demo(factory, now=dt.datetime.now(dt.UTC))


def _demo_patients(session: Session, settings: Settings, now: dt.datetime) -> dict[str, str]:
    """New patients in the Demo Hospital, on its next slots about two days out."""
    hospital = ensure_demo_hospital(session, admin_password=admin_password_from_env())
    org = hospital.organization
    coordinator = StaffPrincipal(
        user_id=hospital.coordinator.id, organization_id=org.id, role=Role.COORDINATOR
    )
    facility = session.scalars(
        select(Facility).where(Facility.organization_id == org.id).order_by(Facility.name)
    ).first()
    procedure = session.scalar(
        select(ProcedureType).where(
            ProcedureType.organization_id == org.id, ProcedureType.code == "COLONOSCOPY"
        )
    )
    if facility is None or procedure is None:  # pragma: no cover - ensure_demo_hospital made them
        raise SystemExit("The Demo Hospital has no facility or colonoscopy.")
    first_slot = demo_slot(now + dt.timedelta(days=2), facility.timezone)
    links = {}
    for index, (key, (first_name, language)) in enumerate(DEMO_PATIENTS.items()):
        patient = PatientService(session).create(
            coordinator,
            PatientCreate(
                first_name=first_name,
                last_name="Demo",
                date_of_birth=dt.date(1980, 1, 1),
                preferred_language=language,
                preferred_channel=MessageChannel.PORTAL,
            ),
            now=now,
        )
        session.flush()
        scheduled_at = demo_slot(first_slot + dt.timedelta(minutes=15 * index), facility.timezone)
        AppointmentService(session).create(
            coordinator,
            AppointmentCreate(
                patient_id=patient.id,
                facility_id=facility.id,
                procedure_type_id=procedure.id,
                scheduled_at=scheduled_at,
                arrival_at=scheduled_at - dt.timedelta(hours=1),
            ),
            now=now,
        )
        link = PatientAccessService(session, settings).issue_magic_link(
            coordinator, patient.id, now=now
        )
        links[key] = link.url
    return links


def main() -> None:
    parser = argparse.ArgumentParser(description="Create patients with fresh portal links.")
    parser.add_argument(
        "--isolated",
        action="store_true",
        help="create fresh test hospitals for the Playwright tests instead",
    )
    isolated = parser.parse_args().isolated
    settings = get_settings()
    if settings.is_production:
        raise SystemExit("Refusing to create test data in production.")
    configure_crypto(settings)
    now = dt.datetime.now(dt.UTC)
    engine = create_db_engine(settings)
    try:
        factory = create_session_factory(engine)
        _ensure_demo(factory)
        with factory() as session:
            if isolated:
                links, coordinator_email = _hospital(session, settings, PATIENTS, now)
                bell_links, bell_email = _hospital(session, settings, BELL_PATIENTS, now)
                links.update(bell_links)
                links["staff_email"] = coordinator_email
                links["bell_staff_email"] = bell_email
                links["staff_password"] = STAFF_PASSWORD
            else:
                links = _demo_patients(session, settings, now)
            session.commit()
    finally:
        engine.dispose()
    if not isolated:
        print(  # Not the last line: the test runner parses only the JSON below.
            f"Sign in to the same hospital with 'Try as Guest', or as {DEMO_ADMIN_EMAIL}."
        )
    # Last line of output: the test runner parses it (logs may come before it).
    print(json.dumps(links))


if __name__ == "__main__":
    main()
