"""Setting up a real hospital: its organization and its first administrator.

Used by the `prefix-admin` command line, which runs on the server with database access, so
there is no staff principal yet. Both steps are audited as the system job "onboarding".
"""

import re
import zoneinfo
from decimal import Decimal

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select
from sqlalchemy.orm import Session

from prefix_api.core.crypto import normalize_email
from prefix_api.core.security import hash_password
from prefix_api.db.models import Organization, User
from prefix_api.domain.enums import AuditAction, Role
from prefix_api.domain.errors import ConflictError, NotFoundError, ValidationFailedError
from prefix_api.domain.principal import SystemPrincipal
from prefix_api.services.audit import record_audit

JOB = "onboarding"
MIN_PASSWORD_LENGTH = 12
_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_COUNTRY = re.compile(r"^[A-Z]{2}$")


def create_organization(
    session: Session,
    *,
    name: str,
    slug: str,
    country_code: str,
    timezone: str,
    baseline_cancellation_rate: Decimal | None = None,
) -> Organization:
    name = name.strip()
    country_code = country_code.strip().upper()
    if not name:
        raise ValidationFailedError("The organization needs a name")
    if not _SLUG.match(slug):
        raise ValidationFailedError(
            "The slug may use lower-case letters, digits and dashes (for example city-hospital)"
        )
    if not _COUNTRY.match(country_code):
        raise ValidationFailedError("The country is a two-letter code, for example IN or US")
    try:
        zoneinfo.ZoneInfo(timezone)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError) as exc:
        raise ValidationFailedError(f"Unknown time zone '{timezone}'") from exc
    if baseline_cancellation_rate is not None and not (
        Decimal(0) <= baseline_cancellation_rate <= Decimal(100)
    ):
        raise ValidationFailedError("The baseline cancellation rate is a percentage from 0 to 100")
    if session.scalar(select(Organization.id).where(Organization.slug == slug)) is not None:
        raise ConflictError(f"An organization with the slug '{slug}' already exists")

    org = Organization(
        name=name,
        slug=slug,
        country_code=country_code,
        default_timezone=timezone,
        is_demo=False,
        baseline_cancellation_rate=baseline_cancellation_rate,
    )
    session.add(org)
    session.flush()
    record_audit(
        session,
        principal=SystemPrincipal(organization_id=org.id, job=JOB),
        action=AuditAction.CREATE,
        entity_type="organization",
        entity_id=org.id,
        purpose="onboarding",
    )
    return org


def check_password(password: str, *, email: str) -> None:
    """The same rule as staff accounts made in the app, plus: not the e-mail address."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationFailedError(
            f"The password must be at least {MIN_PASSWORD_LENGTH} characters"
        )
    if password.strip().lower() == email.strip().lower():
        raise ValidationFailedError("The password must not be the e-mail address")


def create_first_admin(
    session: Session, *, organization_slug: str, email: str, full_name: str, password: str
) -> User:
    """The organization's first ADMIN. Later staff are added by that admin in the app."""
    org = session.scalar(select(Organization).where(Organization.slug == organization_slug))
    if org is None:
        raise NotFoundError(f"No organization with the slug '{organization_slug}'")
    if org.is_demo:
        raise ValidationFailedError(
            "The Demo Hospital's staff come from the seed, not this command"
        )
    existing_admin = session.scalar(
        select(User.id).where(User.organization_id == org.id, User.role == Role.ADMIN).limit(1)
    )
    if existing_admin is not None:
        raise ConflictError(
            "This organization already has an administrator; they add more staff in the app"
        )
    try:
        email = normalize_email(validate_email(email, check_deliverability=False).normalized)
    except EmailNotValidError as exc:
        raise ValidationFailedError("That is not a valid e-mail address") from exc
    full_name = full_name.strip()
    if not full_name:
        raise ValidationFailedError("The administrator needs a name")
    check_password(password, email=email)
    if session.scalar(select(User.id).where(User.email == email)) is not None:
        raise ConflictError("A user with this e-mail already exists")

    user = User(
        organization_id=org.id,
        email=email,
        full_name=full_name,
        role=Role.ADMIN,
        password_hash=hash_password(password),
    )
    session.add(user)
    session.flush()
    record_audit(
        session,
        principal=SystemPrincipal(organization_id=org.id, job=JOB),
        action=AuditAction.CREATE,
        entity_type="user",
        entity_id=user.id,
        purpose="onboarding",
        details={"target_role": Role.ADMIN.value},
    )
    return user
