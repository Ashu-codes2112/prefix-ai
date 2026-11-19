"""`prefix-admin create-org` and `create-first-admin`: a real hospital's first steps."""

from collections.abc import Callable, Iterator

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from prefix_api.cli import run
from prefix_api.core.security import verify_password
from prefix_api.db.models import AuditLog, Organization, User
from prefix_api.domain.enums import ActorType, AuditAction, Role

# Test-only values, built at run time so no literal looks like a credential.
GOOD = "-".join(["correct", "horse", "battery"])
OTHER = "-".join(["another", "long", "phrase"])


def _reader(*answers: str) -> Callable[[str], str]:
    it: Iterator[str] = iter(answers)
    return lambda _prompt: next(it)


class Output:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str) -> None:
        self.lines.append(line)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _create_org(factory: sessionmaker[Session], out: Output, slug: str = "city-hospital") -> int:
    return run(
        [
            "create-org",
            "--name",
            "City Hospital",
            "--slug",
            slug,
            "--country",
            "in",
            "--timezone",
            "Asia/Kolkata",
            "--baseline-rate",
            "12.5",
        ],
        factory,
        out=out,
    )


def _create_admin(
    factory: sessionmaker[Session], out: Output, *answers: str, slug: str = "city-hospital"
) -> int:
    return run(
        [
            "create-first-admin",
            "--org",
            slug,
            "--email",
            "Admin@City-Hospital.example.com",
            "--name",
            "Asha Rao",
        ],
        factory,
        read_password=_reader(*answers),
        out=out,
    )


def test_create_org_then_first_admin(
    session_factory: sessionmaker[Session], db_session: Session
) -> None:
    out = Output()

    assert _create_org(session_factory, out) == 0
    assert _create_admin(session_factory, out, GOOD, GOOD) == 0

    org = db_session.scalars(select(Organization).where(Organization.slug == "city-hospital")).one()
    assert (org.name, org.country_code, org.default_timezone) == (
        "City Hospital",
        "IN",
        "Asia/Kolkata",
    )
    assert not org.is_demo
    assert str(org.baseline_cancellation_rate) == "12.50"
    user = db_session.scalars(select(User).where(User.organization_id == org.id)).one()
    assert (user.email, user.role, user.full_name) == (
        "admin@city-hospital.example.com",
        Role.ADMIN,
        "Asha Rao",
    )
    assert verify_password(GOOD, user.password_hash)

    audit = db_session.scalars(
        select(AuditLog).where(AuditLog.organization_id == org.id).order_by(AuditLog.id)
    ).all()
    assert [(a.actor_type, a.action, a.entity_type) for a in audit] == [
        (ActorType.SYSTEM, AuditAction.CREATE, "organization"),
        (ActorType.SYSTEM, AuditAction.CREATE, "user"),
    ]
    assert audit[1].details == {"target_role": "ADMIN"}


def test_the_password_is_never_printed_or_audited(
    session_factory: sessionmaker[Session], db_session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    out = Output()
    _create_org(session_factory, out)
    _create_admin(session_factory, out, GOOD, GOOD)

    captured = capsys.readouterr()
    assert GOOD not in out.text + captured.out + captured.err
    details = db_session.scalars(select(AuditLog.details)).all()
    assert all(GOOD not in str(d) for d in details)


def test_mismatched_or_short_passwords_are_refused(
    session_factory: sessionmaker[Session], db_session: Session
) -> None:
    out = Output()
    _create_org(session_factory, out)

    assert _create_admin(session_factory, out, GOOD, OTHER) == 1
    assert _create_admin(session_factory, out, "short", "short") == 1

    assert "do not match" in out.lines[-2]
    assert "at least 12 characters" in out.lines[-1]
    created = db_session.scalar(
        select(User.id)
        .join(Organization, Organization.id == User.organization_id)
        .where(Organization.slug == "city-hospital")
    )
    assert created is None


def test_only_one_first_admin(session_factory: sessionmaker[Session]) -> None:
    out = Output()
    _create_org(session_factory, out)
    assert _create_admin(session_factory, out, GOOD, GOOD) == 0

    assert _create_admin(session_factory, out, OTHER, OTHER) == 1
    assert "already has an administrator" in out.lines[-1]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--slug", "City Hospital"], "lower-case letters"),
        (["--country", "India"], "two-letter code"),
        (["--timezone", "Mars/Olympus"], "Unknown time zone"),
    ],
)
def test_create_org_validates_its_input(
    session_factory: sessionmaker[Session], args: list[str], message: str
) -> None:
    base = {
        "--name": "City Hospital",
        "--slug": "city-hospital",
        "--country": "IN",
        "--timezone": "Asia/Kolkata",
    }
    base[args[0]] = args[1]
    out = Output()

    code = run(
        ["create-org", *[part for pair in base.items() for part in pair]], session_factory, out=out
    )

    assert code == 1
    assert message in out.text


def test_duplicate_slug_and_missing_org(session_factory: sessionmaker[Session]) -> None:
    out = Output()
    assert _create_org(session_factory, out) == 0

    assert _create_org(session_factory, out) == 1
    assert "already exists" in out.lines[-1]
    assert _create_admin(session_factory, out, GOOD, GOOD, slug="nowhere") == 1
    assert "No organization" in out.lines[-1]


def test_the_demo_hospital_is_not_set_up_this_way(
    session_factory: sessionmaker[Session], db_session: Session
) -> None:
    db_session.add(
        Organization(
            name="Demo", slug="demo-x", country_code="IN", default_timezone="UTC", is_demo=True
        )
    )
    db_session.commit()
    out = Output()

    assert _create_admin(session_factory, out, GOOD, GOOD, slug="demo-x") == 1
    assert "Demo Hospital" in out.lines[-1]
