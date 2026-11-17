import re

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from prefix_api.core.security import verify_password
from prefix_api.db.models import Facility, Organization, PrepProtocol, ProcedureType, User
from prefix_api.domain.enums import ProtocolStatus, Role, StepCategory
from prefix_api.sample_protocols import SAMPLE_LABEL, SAMPLE_PROTOCOLS, SampleProtocol
from prefix_api.schemas.protocols import StepIn
from prefix_api.seed import (
    DEMO_ADMIN_EMAIL,
    DEMO_BASELINE_RATE,
    DEMO_ORG_SLUG,
    PROCEDURE_TYPES,
    seed,
)
from prefix_api.services.demo import demo_organization, guest_user


def test_seed_creates_demo_org_and_is_idempotent(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SEED_ADMIN_PASSWORD", "seed-password-123")

    first = seed(db_session)
    second = seed(db_session)

    assert first == "seed-password-123"
    assert second is None
    org = db_session.scalar(select(Organization).where(Organization.slug == DEMO_ORG_SLUG))
    assert org is not None
    count = db_session.scalar(
        select(func.count())
        .select_from(ProcedureType)
        .where(ProcedureType.organization_id == org.id)
    )
    assert count == len(PROCEDURE_TYPES)
    assert (db_session.scalar(select(func.count()).select_from(Facility)) or 0) >= 1
    admin = db_session.scalar(select(User).where(User.email == DEMO_ADMIN_EMAIL))
    assert admin is not None
    assert verify_password("seed-password-123", admin.password_hash)


def test_seed_adds_the_sample_protocols_labelled_and_approved_for_the_demo(
    db_session: Session,
) -> None:
    seed(db_session)
    seed(db_session)

    org = db_session.scalar(select(Organization).where(Organization.slug == DEMO_ORG_SLUG))
    assert org is not None
    protocols = db_session.scalars(
        select(PrepProtocol).where(PrepProtocol.organization_id == org.id)
    ).all()
    assert len(protocols) == len(SAMPLE_PROTOCOLS)
    for protocol in protocols:
        # The demo's own clinician signs them off, so the patient demo has prep plans.
        assert protocol.status is ProtocolStatus.APPROVED
        assert protocol.is_sample
        assert SAMPLE_LABEL in protocol.name
        assert protocol.description is not None
        assert SAMPLE_LABEL in protocol.description
        assert protocol.steps


def test_the_admin_and_the_guest_belong_to_the_one_demo_hospital(db_session: Session) -> None:
    seed(db_session)

    org = db_session.scalar(select(Organization).where(Organization.slug == DEMO_ORG_SLUG))
    assert org is not None
    assert org.is_demo
    demo = demo_organization(db_session)
    assert demo is not None
    assert demo.id == org.id
    admin = db_session.scalar(select(User).where(User.email == DEMO_ADMIN_EMAIL))
    guest = guest_user(db_session, org)
    assert admin is not None
    assert guest is not None
    assert admin.organization_id == guest.organization_id == org.id
    assert org.baseline_cancellation_rate == DEMO_BASELINE_RATE


def test_seed_retires_the_old_separate_guest_hospital(db_session: Session) -> None:
    legacy = Organization(
        name="Demo Hospital (guest)",
        slug="guest-demo-hospital",
        country_code="IN",
        default_timezone="Asia/Kolkata",
        is_demo=True,
    )
    db_session.add(legacy)
    db_session.flush()
    old_guest = User(
        organization_id=legacy.id,
        email="guest@guest-demo-hospital.demo.example.com",
        full_name="Old guest",
        role=Role.DEMO_VIEWER,
        password_hash="x",
    )
    db_session.add(old_guest)
    db_session.flush()

    seed(db_session)

    db_session.refresh(legacy)
    db_session.refresh(old_guest)
    assert legacy.is_demo is False
    assert old_guest.is_active is False
    demo = demo_organization(db_session)
    assert demo is not None
    assert demo.slug == DEMO_ORG_SLUG


@pytest.mark.parametrize("sample", SAMPLE_PROTOCOLS, ids=lambda s: s.procedure_code)
def test_sample_protocol_content_is_well_formed(sample: SampleProtocol) -> None:
    for step in sample.steps:
        StepIn(
            offset_minutes=step.offset_minutes,
            title=step.title,
            instruction_text=step.instruction_text,
            category=step.category,
            requires_confirmation=step.requires_confirmation,
            is_critical=step.is_critical,
            conditions=step.conditions,
        )
        # Samples never prescribe: no doses, and medicine steps defer to the care team.
        assert not re.search(r"\d+\s?(mg|ml|units?|tablets?)\b", step.instruction_text, re.I)
        if step.category is StepCategory.MEDICATION:
            assert "care team" in step.instruction_text
