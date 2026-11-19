"""`prefix-admin`: set up a real hospital from the server's command line.

    uv run prefix-admin create-org --name "City Hospital" --slug city-hospital \\
        --country IN --timezone Asia/Kolkata [--baseline-rate 12.5]
    uv run prefix-admin create-first-admin --org city-hospital \\
        --email admin@city-hospital.example --name "Asha Rao"

The password is typed at a hidden prompt, twice. It is never echoed, logged or printed, and
there is deliberately no flag for it, so it cannot end up in shell history.
"""

import argparse
import getpass
import sys
from collections.abc import Callable, Sequence
from decimal import Decimal, InvalidOperation

from prefix_api.core.config import get_settings
from prefix_api.core.crypto import configure_crypto
from prefix_api.core.logging import configure_logging, get_logger
from prefix_api.db.session import SessionFactory, create_db_engine, create_session_factory
from prefix_api.domain.errors import DomainError, ValidationFailedError
from prefix_api.services import onboarding

type PasswordReader = Callable[[str], str]


def _rate(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("the baseline rate is a number, e.g. 12.5") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prefix-admin", description="Set up a hospital in Prefix AI."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    org = commands.add_parser("create-org", help="Create a hospital (an organization).")
    org.add_argument("--name", required=True, help='Display name, e.g. "City Hospital"')
    org.add_argument("--slug", required=True, help="Short id, e.g. city-hospital")
    org.add_argument("--country", required=True, help="Two-letter country code, e.g. IN")
    org.add_argument("--timezone", required=True, help="IANA zone, e.g. Asia/Kolkata")
    org.add_argument(
        "--baseline-rate",
        type=_rate,
        help="Cancellation rate (%%) before Prefix AI, for Analytics",
    )

    admin = commands.add_parser(
        "create-first-admin", help="Create the hospital's first administrator."
    )
    admin.add_argument("--org", required=True, help="The organization's slug")
    admin.add_argument("--email", required=True)
    admin.add_argument("--name", required=True, help="The administrator's full name")
    return parser


def _read_new_password(read_password: PasswordReader) -> str:
    first = read_password("Password (at least 12 characters, not shown): ")
    second = read_password("Type it again: ")
    if first != second:
        raise ValidationFailedError("The two passwords do not match")
    return first


def run(
    argv: Sequence[str] | None,
    session_factory: SessionFactory,
    *,
    read_password: PasswordReader = getpass.getpass,
    out: Callable[[str], None] = print,
) -> int:
    args = _parser().parse_args(argv)
    try:
        with session_factory() as session:
            if args.command == "create-org":
                org = onboarding.create_organization(
                    session,
                    name=args.name,
                    slug=args.slug,
                    country_code=args.country,
                    timezone=args.timezone,
                    baseline_cancellation_rate=args.baseline_rate,
                )
                session.commit()
                out(f"Created organization '{org.name}' (slug {org.slug}).")
                out(
                    f"Next: prefix-admin create-first-admin --org {org.slug} --email ... --name ..."
                )
            else:
                password = _read_new_password(read_password)
                user = onboarding.create_first_admin(
                    session,
                    organization_slug=args.org,
                    email=args.email,
                    full_name=args.name,
                    password=password,
                )
                session.commit()
                out(f"Created administrator {user.email}. They can sign in now.")
    except DomainError as exc:
        out(f"Error: {exc.message}")
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    configure_crypto(settings)
    engine = create_db_engine(settings)
    try:
        code = run(argv, create_session_factory(engine))
    finally:
        engine.dispose()
    get_logger(__name__).info("cli.finished", exit_code=code)
    sys.exit(code)


if __name__ == "__main__":
    main()
