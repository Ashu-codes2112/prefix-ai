"""Run the Celery worker and beat locally, on Windows without Docker or Redis. Development only.

    uv run python scripts/dev_worker.py

The worker and beat always share one broker, chosen by the settings:

- Redis, when REDIS_URL is set (and CELERY_BROKER is not "postgres").
- Otherwise PostgreSQL: kombu's SQLAlchemy transport as the broker and Celery's database
  result backend, in a database of its own next to the app's ("<name>_celery", created here
  if needed). Leave REDIS_URL empty, or put CELERY_BROKER=postgres in .env, to get this.

The worker runs with --pool=solo, the pool that works on Windows. Stop both with Ctrl+C.
Never use the PostgreSQL broker in production: the settings refuse it outside development
(docs/DEPLOYMENT.md).
"""

import os
import socket
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
SCHEDULE = ROOT / ".devdb" / "celerybeat-schedule"


def _ensure_database(url: str) -> None:
    """Create the worker's database on the dev server if it does not exist yet."""
    target = make_url(url)
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            exists = connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": target.database},
            )
            if not exists:
                connection.execute(text(f'CREATE DATABASE "{target.database}"'))
                print(f"Created the worker's database {target.database}.")
    finally:
        admin.dispose()


def _prepare_tables() -> None:
    """Create the broker's and the result backend's tables once. Left to the worker and beat,
    both try at the same moment and one of them stops with a duplicate-table error."""
    from prefix_worker.celery_app import celery_app

    with celery_app.connection_for_write() as connection, connection.channel() as channel:
        channel.queue_declare(queue=celery_app.conf.task_default_queue)
    celery_app.backend.ResultSession().close()


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def main() -> None:
    # Celery's command line would read CELERY_BROKER from the environment as a broker URL, so
    # the worker and beat get the choice as PREFIX_CELERY_BROKER instead (the settings read
    # both names). The value in .env is unaffected: Celery does not read that file.
    shell_choice = os.environ.pop("CELERY_BROKER", None)
    if shell_choice:
        os.environ.setdefault("PREFIX_CELERY_BROKER", shell_choice)

    from prefix_api.core.config import get_settings
    from prefix_worker.celery_app import celery_database_url

    settings = get_settings()
    if settings.is_production:
        raise SystemExit("Refusing to run the development worker in production.")
    if settings.celery_uses_postgres:
        database = celery_database_url(settings)
        _ensure_database(database)
        _prepare_tables()
        broker = f"PostgreSQL ({make_url(database).database}, development only)"
        os.environ["PREFIX_CELERY_BROKER"] = "postgres"
    else:
        redis = settings.redis_url
        assert redis is not None  # noqa: S101 - celery_uses_postgres is False only with Redis
        if not _reachable(redis.host or "localhost", redis.port or 6379):
            raise SystemExit(
                f"Redis at {redis.host}:{redis.port} is not reachable. Without Redis, leave "
                "REDIS_URL empty or put CELERY_BROKER=postgres in .env, then run this again."
            )
        broker = "Redis"
        os.environ["PREFIX_CELERY_BROKER"] = "redis"
    SCHEDULE.parent.mkdir(exist_ok=True)

    celery = [sys.executable, "-m", "celery", "--app", "prefix_worker.celery_app"]
    commands = {
        "worker": [*celery, "worker", "--pool=solo", "--loglevel", "INFO"],
        "beat": [*celery, "beat", "--schedule", str(SCHEDULE), "--loglevel", "INFO"],
    }
    # Fixed argument lists, no shell: nothing here comes from outside the repository.
    processes = {
        name: subprocess.Popen(command, cwd=ROOT)  # noqa: S603
        for name, command in commands.items()
    }
    pids = ", ".join(f"{name} {process.pid}" for name, process in processes.items())
    print(f"Celery worker and beat started on {broker} ({pids}).", flush=True)
    print(
        "Messaging:",
        "live (Twilio)" if settings.messaging_live else "demo mode (logged, not sent)",
        flush=True,
    )
    print("Stop both with Ctrl+C (or close this window).", flush=True)
    try:
        for process in processes.values():
            process.wait()
    except KeyboardInterrupt:
        print("Stopping the worker and beat...", flush=True)
    finally:
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
        for process in processes.values():
            process.wait()


if __name__ == "__main__":
    main()
