"""The Demo Hospital: the one organization behind the seeded admin, "Try as Guest" and the
patient demo. Run with `python -m prefix_api.seed` (or `make seed`).

Idempotent: re-running keeps existing rows and passwords. It refuses to run in production.
The hospital's synthetic patients come from `services/demo.py`; `main` fills them in the
first time, and `make seed-demo` (or the nightly reset) regenerates them.

Earlier versions kept the guest demo in a second organization ("guest-demo-hospital"). The
first run of this version retires it: it stops being the demo and its accounts are switched
off, so every demo login lands in the same hospital.
"""

import datetime as dt
import os
import secrets
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from prefix_api.core.config import get_settings
from prefix_api.core.crypto import configure_crypto, normalize_email
from prefix_api.core.logging import configure_logging, get_logger
from prefix_api.core.security import hash_password
from prefix_api.db.models import (
    Facility,
    Organization,
    Patient,
    PrepProtocol,
    PrepStep,
    ProcedureType,
    User,
)
from prefix_api.db.session import create_db_engine, create_session_factory
from prefix_api.domain.enums import ProtocolStatus, Role
from prefix_api.domain.principal import StaffPrincipal
from prefix_api.sample_protocols import SAMPLE_PROTOCOLS
from prefix_api.services.embeddings import get_embedding_provider
from prefix_api.services.protocols import ProtocolService

DEMO_ORG_SLUG = "demo-hospital"
DEMO_ORG_NAME = "Demo Hospital"
DEMO_EMAIL_DOMAIN = "demo-hospital.example.com"
DEMO_ADMIN_EMAIL = f"admin@{DEMO_EMAIL_DOMAIN}"
# Its cancellation rate before Prefix AI, so Analytics can estimate what was avoided.
DEMO_BASELINE_RATE = Decimal("14.0")
DEMO_TIMEZONE = "Asia/Kolkata"

PROCEDURE_TYPES = [
    # code, name, default minutes, OR cost per minute (INR), sedation
    ("COLONOSCOPY", "Colonoscopy", 45, Decimal("150.00"), True),
    ("GENERAL_SURGERY", "General surgery under anesthesia", 120, Decimal("600.00"), True),
    ("CT_CONTRAST", "CT scan with contrast", 30, Decimal("100.00"), False),
    ("MRI", "MRI scan", 45, Decimal("120.00"), False),
]
FACILITIES = [
    ("Demo Main Campus", "2 Demo Road, Bengaluru"),
    ("Demo Day Care Centre", "9 Sample Street, Bengaluru"),
]


@dataclass(frozen=True, slots=True)
class DemoHospital:
    organization: Organization
    admin: User
    coordinator: User
    clinician: User
    guest: User
    # True when this run created the admin (so its password is worth printing).
    admin_created: bool


def _demo_organization(session: Session) -> Organization:
    """The Demo Hospital, created if needed. Any other demo organization is retired."""
    org = session.scalar(select(Organization).where(Organization.slug == DEMO_ORG_SLUG))
    if org is None:
        org = Organization(
            name=DEMO_ORG_NAME,
            slug=DEMO_ORG_SLUG,
            country_code="IN",
            default_timezone=DEMO_TIMEZONE,
        )
        session.add(org)
    org.is_demo = True
    if org.baseline_cancellation_rate is None:
        org.baseline_cancellation_rate = DEMO_BASELINE_RATE
    session.flush()
    for legacy in session.scalars(
        select(Organization).where(Organization.is_demo.is_(True), Organization.id != org.id)
    ):
        legacy.is_demo = False
        session.execute(
            update(User).where(User.organization_id == legacy.id).values(is_active=False)
        )
    return org


def _staff(
    session: Session, org: Organization, role: Role, local_part: str, password: str
) -> tuple[User, bool]:
    email = normalize_email(f"{local_part}@{DEMO_EMAIL_DOMAIN}")
    user = session.scalar(select(User).where(User.email == email))
    if user is not None:
        return user, False
    user = User(
        organization_id=org.id,
        email=email,
        full_name=f"Demo {role.value.title().replace('_', ' ')}",
        role=role,
        password_hash=hash_password(password),
    )
    session.add(user)
    session.flush()
    return user, True


def ensure_demo_hospital(session: Session, *, admin_password: str) -> DemoHospital:
    """Facilities, procedures, approved protocols and staff for the Demo Hospital.

    The guest's and the other staff's passwords are random: guests never use one, and the
    demo is meant to be explored as the guest or the admin.
    """
    org = _demo_organization(session)

    named = set(session.scalars(select(Facility.name).where(Facility.organization_id == org.id)))
    for name, address in FACILITIES:
        if name not in named:
            session.add(
                Facility(
                    organization_id=org.id,
                    name=name,
                    timezone=DEMO_TIMEZONE,
                    address=address,
                    maps_url=f"https://maps.example.com/{uuid.uuid5(org.id, name)}",
                    phone="+911100000000",
                )
            )

    codes = set(
        session.scalars(select(ProcedureType.code).where(ProcedureType.organization_id == org.id))
    )
    for code, name, minutes, cost, sedation in PROCEDURE_TYPES:
        if code not in codes:
            session.add(
                ProcedureType(
                    organization_id=org.id,
                    code=code,
                    name=name,
                    default_duration_minutes=minutes,
                    or_cost_per_minute=cost,
                    requires_sedation=sedation,
                )
            )
    session.flush()

    admin, admin_created = _staff(session, org, Role.ADMIN, "admin", admin_password)
    coordinator, _ = _staff(
        session, org, Role.COORDINATOR, "coordinator", secrets.token_urlsafe(16)
    )
    clinician, _ = _staff(session, org, Role.CLINICIAN, "clinician", secrets.token_urlsafe(16))
    guest, _ = _staff(session, org, Role.DEMO_VIEWER, "guest", secrets.token_urlsafe(16))

    seed_sample_protocols(session, org)
    session.flush()
    # The demo's clinician signs off the sample protocols, so the demo can send prep plans.
    # This only ever happens in the Demo Hospital; a real hospital approves its own.
    signer = StaffPrincipal(user_id=clinician.id, organization_id=org.id, role=Role.CLINICIAN)
    protocols = ProtocolService(session, get_embedding_provider("local"))
    drafts = session.scalars(
        select(PrepProtocol).where(
            PrepProtocol.organization_id == org.id, PrepProtocol.status == ProtocolStatus.DRAFT
        )
    ).all()
    for protocol in drafts:
        protocols.approve(signer, protocol.id, acknowledge_sample=True, now=dt.datetime.now(dt.UTC))
    session.flush()
    return DemoHospital(
        organization=org,
        admin=admin,
        coordinator=coordinator,
        clinician=clinician,
        guest=guest,
        admin_created=admin_created,
    )


def seed_sample_protocols(session: Session, org: Organization) -> None:
    """Create each SAMPLE protocol as a DRAFT that a clinician must review and approve."""
    existing = set(
        session.scalars(select(PrepProtocol.name).where(PrepProtocol.organization_id == org.id))
    )
    for sample in SAMPLE_PROTOCOLS:
        if sample.name in existing:
            continue
        procedure_type = session.scalar(
            select(ProcedureType).where(
                ProcedureType.organization_id == org.id,
                ProcedureType.code == sample.procedure_code,
            )
        )
        if procedure_type is None:
            continue
        session.add(
            PrepProtocol(
                organization_id=org.id,
                family_id=uuid.uuid7(),
                version=1,
                procedure_type_id=procedure_type.id,
                name=sample.name,
                description=sample.description,
                status=ProtocolStatus.DRAFT,
                is_sample=True,
                steps=[
                    PrepStep(
                        organization_id=org.id,
                        sort_order=index,
                        offset_minutes=step.offset_minutes,
                        title=step.title,
                        instruction_text=step.instruction_text,
                        category=step.category,
                        requires_confirmation=step.requires_confirmation,
                        is_critical=step.is_critical,
                        conditions=step.conditions,
                    )
                    for index, step in enumerate(sample.steps)
                ],
            )
        )


def admin_password_from_env() -> str:
    """SEED_ADMIN_PASSWORD (or DEMO_ADMIN_PASSWORD) if set, otherwise a random one."""
    return (
        os.environ.get("SEED_ADMIN_PASSWORD")
        or os.environ.get("DEMO_ADMIN_PASSWORD")
        or secrets.token_urlsafe(12)
    )


def seed(session: Session) -> str | None:
    """Create the Demo Hospital. Returns the admin's password if the admin was just created."""
    password = admin_password_from_env()
    hospital = ensure_demo_hospital(session, admin_password=password)
    session.commit()
    return password if hospital.admin_created else None


def main() -> None:
    # Imported here: the patient generator builds on this module.
    from prefix_api.services.demo import populate_demo

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    configure_crypto(settings)
    logger = get_logger(__name__)
    if settings.is_production:
        raise SystemExit("Refusing to seed a production database.")

    engine = create_db_engine(settings)
    try:
        factory = create_session_factory(engine)
        with factory() as session:
            password = seed(session)
            org = session.scalar(select(Organization).where(Organization.slug == DEMO_ORG_SLUG))
            empty = org is None or (
                session.scalar(select(Patient.id).where(Patient.organization_id == org.id).limit(1))
                is None
            )
        if empty:
            # A first run also fills in the synthetic patients, so the demo works at once.
            populate_demo(factory, now=dt.datetime.now(dt.UTC))
    finally:
        engine.dispose()

    logger.info("seed.completed")
    if password is not None:
        # Development only: printed once so the developer can log in.
        print(f"Demo admin: {DEMO_ADMIN_EMAIL} / {password}")  # noqa: T201


if __name__ == "__main__":
    main()
