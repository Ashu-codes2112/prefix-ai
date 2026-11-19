import datetime as dt
import secrets
from functools import lru_cache

from celery import shared_task

from prefix_api.core.config import get_settings
from prefix_api.core.logging import get_logger
from prefix_api.db.session import SessionFactory, create_db_engine, create_session_factory
from prefix_api.services.demo import populate_demo
from prefix_api.services.dispatch import dispatch_due_reminders
from prefix_api.services.integrations import sync_all
from prefix_api.services.messaging.base import MessagingProvider
from prefix_api.services.messaging.providers import build_messaging_provider
from prefix_api.services.retention import purge_expired
from prefix_api.services.risk import refresh as refresh_risk
from prefix_api.services.step_translations import warm_step_translations
from prefix_api.services.translation import Translator, build_translator
from prefix_api.services.webhooks import deliver_due

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def _session_factory() -> SessionFactory:
    return create_session_factory(create_db_engine(get_settings()))


@lru_cache(maxsize=1)
def _provider() -> MessagingProvider:
    return build_messaging_provider(get_settings())


@lru_cache(maxsize=1)
def _translator() -> Translator:
    return build_translator(get_settings())


@shared_task(name="prefix_worker.demo.ping")
def ping() -> str:
    """Demo task proving the worker, broker, and beat schedule are wired up."""
    logger.info("demo.ping")
    return "pong"


@shared_task(name="prefix_worker.reminders.dispatch", ignore_result=True)
def dispatch_reminders() -> dict[str, int]:
    """Send every due prep reminder exactly once. Scheduled every minute by beat."""
    return dispatch_due_reminders(
        _session_factory(),
        _provider(),
        _translator(),
        now=dt.datetime.now(dt.UTC),
        batch_size=get_settings().dispatch_batch_size,
    )


@shared_task(name="prefix_worker.translations.warm", ignore_result=True)
def warm_translations() -> dict[str, int]:
    """Pre-translate approved steps for upcoming non-English patients, so the portal and
    reminders can use them without waiting for the LLM."""
    return warm_step_translations(_session_factory(), _translator(), now=dt.datetime.now(dt.UTC))


@shared_task(name="prefix_worker.demo.reset", ignore_result=True)
def reset_demo_nightly() -> dict[str, int]:
    """Regenerate the guest demo's synthetic patients. Scheduled nightly by beat."""
    result = populate_demo(
        _session_factory(),
        now=dt.datetime.now(dt.UTC),
        admin_password=secrets.token_urlsafe(12),
    )
    return {"patients": result.patients, "appointments": result.appointments}


@shared_task(name="prefix_worker.risk.refresh", ignore_result=True)
def refresh_risk_scores() -> dict[str, int]:
    """Score upcoming procedures and open a task for each new HIGH-risk one. Every 15 minutes."""
    result = refresh_risk(_session_factory(), now=dt.datetime.now(dt.UTC))
    return {"scored": result.scored, "high": result.high, "tasks_opened": result.tasks_opened}


@shared_task(name="prefix_worker.retention.purge", ignore_result=True)
def purge_expired_records() -> dict[str, int]:
    """Erase patients past their hospital's retention period (docs/COMPLIANCE.md). Nightly."""
    result = purge_expired(_session_factory(), now=dt.datetime.now(dt.UTC))
    logger.info("retention.purged", **result)
    return result


@shared_task(name="prefix_worker.fhir.sync", ignore_result=True)
def sync_fhir() -> dict[str, int]:
    """Read what changed in each hospital's EHR since the last run (FHIR R4). Every 15 minutes."""
    result = sync_all(_session_factory(), now=dt.datetime.now(dt.UTC))
    logger.info("fhir.synced", **result)
    return result


@shared_task(name="prefix_worker.webhooks.deliver", ignore_result=True)
def deliver_webhooks() -> dict[str, int]:
    """Post due appointment-change webhooks, signed; retries back off. Every minute."""
    result = deliver_due(_session_factory(), now=dt.datetime.now(dt.UTC))
    return {"delivered": result.delivered, "retrying": result.retrying, "failed": result.failed}
