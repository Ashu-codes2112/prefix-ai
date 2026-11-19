import os

import pytest

from prefix_api.core.config import Settings
from prefix_worker.celery_app import (
    DEMO_PING_INTERVAL_SECONDS,
    REMINDER_DISPATCH_INTERVAL_SECONDS,
    TRANSLATION_WARM_INTERVAL_SECONDS,
    celery_app,
    celery_urls,
    create_celery_app,
)
from prefix_worker.tasks import ping


def test_ping_returns_pong_when_run_locally() -> None:
    result = ping.apply()

    assert result.successful()
    assert result.get() == "pong"


def test_ping_is_registered_with_the_app() -> None:
    celery_app.loader.import_default_modules()

    assert "prefix_worker.demo.ping" in celery_app.tasks


def test_beat_schedules_the_demo_task() -> None:
    entry = celery_app.conf.beat_schedule["demo-ping"]

    assert entry["task"] == "prefix_worker.demo.ping"
    assert entry["schedule"] == DEMO_PING_INTERVAL_SECONDS


def test_only_json_is_accepted() -> None:
    assert celery_app.conf.accept_content == ["json"]
    assert celery_app.conf.task_serializer == "json"


def test_beat_dispatches_reminders_every_minute() -> None:
    entry = celery_app.conf.beat_schedule["dispatch-reminders"]

    assert entry["task"] == "prefix_worker.reminders.dispatch"
    assert entry["schedule"] == REMINDER_DISPATCH_INTERVAL_SECONDS == 60.0
    celery_app.loader.import_default_modules()
    assert "prefix_worker.reminders.dispatch" in celery_app.tasks


def test_beat_warms_step_translations() -> None:
    entry = celery_app.conf.beat_schedule["warm-translations"]

    assert entry["task"] == "prefix_worker.translations.warm"
    assert entry["schedule"] == TRANSLATION_WARM_INTERVAL_SECONDS
    celery_app.loader.import_default_modules()
    assert "prefix_worker.translations.warm" in celery_app.tasks


def test_beat_resets_the_guest_demo_nightly() -> None:
    entry = celery_app.conf.beat_schedule["reset-demo-nightly"]

    assert entry["task"] == "prefix_worker.demo.reset"
    assert entry["schedule"].hour == {21}
    assert entry["schedule"].minute == {30}
    celery_app.loader.import_default_modules()
    assert "prefix_worker.demo.reset" in celery_app.tasks


def _settings(**overrides: object) -> Settings:
    # The test keys come from conftest.py, which puts them in the environment.
    values: dict[str, object] = {
        "app_env": "test",
        "llm_model": "test-model",
        "jwt_secret": os.environ["JWT_SECRET"],
        "pii_encryption_keys": [os.environ["PII_ENCRYPTION_KEYS"]],
        "pii_blind_index_key": os.environ["PII_BLIND_INDEX_KEY"],
        "database_url": "postgresql+psycopg://prefix@db.local:5432/prefix",
        # Given here so a developer's .env never changes the result.
        "redis_url": None,
        "celery_broker": "auto",
        "prefix_celery_broker": None,
        **overrides,
    }
    return Settings.model_validate(values)


def test_redis_is_the_broker_and_the_result_backend_when_it_is_set() -> None:
    broker, backend = celery_urls(_settings(redis_url="redis://cache:6379/0"))

    assert broker == backend == "redis://cache:6379/0"


def test_without_redis_the_worker_uses_its_own_postgres_database() -> None:
    broker, backend = celery_urls(_settings())

    database = "postgresql+psycopg://prefix@db.local:5432/prefix_celery"
    assert (broker, backend) == (f"sqla+{database}", f"db+{database}")


def test_celery_broker_postgres_wins_over_redis_for_local_runs() -> None:
    broker, _ = celery_urls(_settings(redis_url="redis://cache:6379/0", celery_broker="postgres"))

    assert broker.startswith("sqla+postgresql+psycopg://")


def test_the_launchers_choice_wins_over_dotenv() -> None:
    # scripts/dev_worker.py passes the choice as PREFIX_CELERY_BROKER: Celery's own command line
    # would read CELERY_BROKER as a broker URL.
    broker, _ = celery_urls(
        _settings(redis_url="redis://cache:6379/0", prefix_celery_broker="postgres")
    )

    assert broker.startswith("sqla+postgresql+psycopg://")


def test_a_broker_choice_copied_in_by_celerys_command_line_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `celery` reads CELERY_BROKER=postgres as a broker URL and copies it to CELERY_BROKER_URL.
    monkeypatch.setenv("CELERY_BROKER_URL", "postgres")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://cache:6379/1")

    app = create_celery_app(_settings(redis_url="redis://cache:6379/0"))

    assert "CELERY_BROKER_URL" not in os.environ
    assert app.conf.broker_url == "redis://cache:6379/0"
    # A real URL that someone set on purpose is left alone.
    assert os.environ["CELERY_RESULT_BACKEND"] == "redis://cache:6379/1"
