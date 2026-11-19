import os
from typing import Any

from celery import Celery
from celery.schedules import crontab
from celery.signals import setup_logging
from sqlalchemy.engine import make_url

from prefix_api.core.config import Settings, get_settings
from prefix_api.core.logging import configure_logging

DEMO_PING_INTERVAL_SECONDS = 300.0
REMINDER_DISPATCH_INTERVAL_SECONDS = 60.0
TRANSLATION_WARM_INTERVAL_SECONDS = 600.0
RISK_REFRESH_INTERVAL_SECONDS = 900.0


def celery_database_url(settings: Settings) -> str:
    """The worker's own database on the app's PostgreSQL server ("<name>_celery"), so the
    broker's and the result backend's tables stay out of the app's schema and its migrations."""
    url = make_url(str(settings.database_url))
    return url.set(database=f"{url.database}_celery").render_as_string(hide_password=False)


def celery_urls(settings: Settings) -> tuple[str, str]:
    """The broker and the result backend. Redis, except in local development without it
    (REDIS_URL unset, or CELERY_BROKER=postgres): then kombu's SQLAlchemy transport and Celery's
    database backend, both on ``celery_database_url``. Settings refuse that outside development.
    """
    if settings.celery_uses_postgres:
        database = celery_database_url(settings)
        return f"sqla+{database}", f"db+{database}"
    assert settings.redis_url is not None  # noqa: S101 - celery_uses_postgres covers None
    redis = settings.redis_url.unicode_string()
    return redis, redis


# Our CELERY_BROKER setting is a choice (auto, redis or postgres), never a URL. Celery's own
# command line reads a CELERY_BROKER environment variable as its broker URL and copies it into
# CELERY_BROKER_URL, which wins over the broker set below. Docker Compose passes every line of
# .env to the containers, so drop such a copy rather than connect to "amqp://postgres:5672".
_BROKER_CHOICES = frozenset({"auto", "redis", "postgres"})


def _drop_broker_choice_from_cli() -> None:
    for name in ("CELERY_BROKER_URL", "CELERY_RESULT_BACKEND"):
        if os.environ.get(name, "").lower() in _BROKER_CHOICES:
            del os.environ[name]


def create_celery_app(settings: Settings) -> Celery:
    _drop_broker_choice_from_cli()
    broker_url, backend_url = celery_urls(settings)
    app = Celery("prefix_worker", broker=broker_url, backend=backend_url)
    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_track_started=True,
        result_expires=3600,
        broker_connection_retry_on_startup=True,
        worker_hijack_root_logger=False,
        beat_schedule={
            "demo-ping": {
                "task": "prefix_worker.demo.ping",
                "schedule": DEMO_PING_INTERVAL_SECONDS,
            },
            "dispatch-reminders": {
                "task": "prefix_worker.reminders.dispatch",
                "schedule": REMINDER_DISPATCH_INTERVAL_SECONDS,
                # A run that misses its slot is pointless: the next one picks everything up.
                "options": {"expires": REMINDER_DISPATCH_INTERVAL_SECONDS},
            },
            "refresh-risk-scores": {
                "task": "prefix_worker.risk.refresh",
                "schedule": RISK_REFRESH_INTERVAL_SECONDS,
                "options": {"expires": RISK_REFRESH_INTERVAL_SECONDS},
            },
            # The guest demo starts fresh every night at 03:00 in India (21:30 UTC).
            "reset-demo-nightly": {
                "task": "prefix_worker.demo.reset",
                "schedule": crontab(hour=21, minute=30),
            },
            # Retention purge at 02:15 in India (20:45 UTC), after the evening's last reminders.
            "retention-purge": {
                "task": "prefix_worker.retention.purge",
                "schedule": crontab(hour=20, minute=45),
            },
            "warm-translations": {
                "task": "prefix_worker.translations.warm",
                "schedule": TRANSLATION_WARM_INTERVAL_SECONDS,
                "options": {"expires": TRANSLATION_WARM_INTERVAL_SECONDS},
            },
        },
    )
    app.autodiscover_tasks(["prefix_worker"], related_name="tasks", force=True)
    return app


@setup_logging.connect
def _configure_logging(**_kwargs: Any) -> None:
    # Connecting to this signal stops Celery from installing its own log handlers.
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)


celery_app = create_celery_app(get_settings())
