"""Create or refresh the Demo Hospital's synthetic patients. Run with `make seed-demo`:

    uv run python -m prefix_api.seed_demo

Safe to re-run: it regenerates the synthetic patients, bookings, reminders and last month's
history, and keeps the hospital's staff accounts and approved protocols. It refuses to run in
production. The admin's password is printed only when the account is first created; set
SEED_ADMIN_PASSWORD (or DEMO_ADMIN_PASSWORD) to choose it.
"""

import datetime as dt
import sys

from sqlalchemy import select

from prefix_api.core.config import get_settings
from prefix_api.core.crypto import configure_crypto
from prefix_api.core.logging import configure_logging
from prefix_api.db.models import User
from prefix_api.db.session import create_db_engine, create_session_factory
from prefix_api.seed import DEMO_ADMIN_EMAIL, admin_password_from_env
from prefix_api.services.demo import populate_demo


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    configure_crypto(settings)
    if settings.is_production:
        sys.exit("Refusing to seed the demo hospital in production.")

    engine = create_db_engine(settings)
    try:
        factory = create_session_factory(engine)
        with factory() as session:
            admin_existed = (
                session.scalar(select(User.id).where(User.email == DEMO_ADMIN_EMAIL)) is not None
            )
        password = admin_password_from_env()
        result = populate_demo(factory, now=dt.datetime.now(dt.UTC), admin_password=password)
    finally:
        engine.dispose()

    print(  # noqa: T201 - a developer-facing summary
        f"Demo Hospital ready: {result.patients} patients, {result.appointments} bookings, "
        f"{result.reminders_sent} reminders, {result.answers} answers, "
        f"{result.escalations} escalations."
    )
    print("Guest sign-in: the 'Try as Guest' button at http://localhost:3000/login")  # noqa: T201
    if not admin_existed:
        print(f"Demo admin (same hospital): {DEMO_ADMIN_EMAIL} / {password}")  # noqa: T201


if __name__ == "__main__":
    main()
