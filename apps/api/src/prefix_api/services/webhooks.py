"""Outbound webhooks: appointment status changes, posted to the hospital's systems.

When an appointment's status changes, through any code path, the same transaction writes a
``WebhookDelivery`` for each enabled endpoint of the hospital (an outbox). So a change is never
sent unless it was saved, and never lost if the receiver is down. The worker posts due
deliveries every minute and retries with backoff.

Each POST carries:
- ``X-Prefix-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>.<raw body>">`` under the
  endpoint's secret. Receivers recompute it, compare in constant time and reject old stamps.
- ``X-Prefix-Event`` and ``X-Prefix-Delivery`` (the delivery id; the same on every retry, so a
  receiver can ignore repeats).

Payloads hold ids, statuses and times only: never names, contact details or free text.
The synthetic Demo Hospital never sends webhooks.
"""

import datetime as dt
import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass
from typing import Any

import httpx2
from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session, UOWTransaction

from prefix_api.db.models import Appointment, Organization, WebhookDelivery, WebhookEndpoint
from prefix_api.db.session import SessionFactory
from prefix_api.domain.enums import AppointmentStatus, DeliveryStatus

EVENT_STATUS_CHANGED = "appointment.status_changed"
# After each failed attempt, wait this long; then give up.
BACKOFF = (
    dt.timedelta(minutes=1),
    dt.timedelta(minutes=5),
    dt.timedelta(minutes=30),
    dt.timedelta(hours=2),
    dt.timedelta(hours=6),
    dt.timedelta(hours=12),
)
MAX_ATTEMPTS = len(BACKOFF) + 1
TIMEOUT_SECONDS = 10.0


def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def signature(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"t={timestamp},v1={mac.hexdigest()}"


def verify_signature(
    secret: str, header: str, body: bytes, *, now: int, tolerance_seconds: int = 300
) -> bool:
    """What a receiver does; used by the tests and shown in docs/INTEGRATIONS.md."""
    parts = dict(item.split("=", 1) for item in header.split(",") if "=" in item)
    try:
        timestamp = int(parts["t"])
    except KeyError, ValueError:
        return False
    if abs(now - timestamp) > tolerance_seconds:
        return False
    expected = signature(secret, timestamp, body).split("v1=", 1)[1]
    return hmac.compare_digest(expected, parts.get("v1", ""))


def _payload(
    delivery_id: uuid.UUID,
    appointment: Appointment,
    previous: AppointmentStatus | None,
    now: dt.datetime,
) -> dict[str, Any]:
    reason = appointment.cancellation_reason
    return {
        "id": str(delivery_id),
        "type": EVENT_STATUS_CHANGED,
        "occurred_at": now.isoformat(),
        "data": {
            "appointment_id": str(appointment.id),
            "external_id": appointment.external_id,
            "status": appointment.status.value,
            "previous_status": previous.value if previous else None,
            "scheduled_at": appointment.scheduled_at.isoformat(),
            "cancellation_reason": reason.value if reason else None,
        },
    }


# The outbox ------------------------------------------------------------------------------------


def _status_changes(session: Session) -> list[tuple[Appointment, AppointmentStatus | None]]:
    changes: list[tuple[Appointment, AppointmentStatus | None]] = []
    for obj in session.dirty:
        if not isinstance(obj, Appointment):
            continue
        history = inspect(obj).attrs.status.history
        if not history.has_changes():
            continue
        previous = history.deleted[0] if history.deleted else None
        if previous != obj.status:
            changes.append((obj, previous))
    return changes


def _enqueue(session: Session, _flush: UOWTransaction, _instances: object) -> None:
    changes = _status_changes(session)
    if not changes:
        return
    now = dt.datetime.now(dt.UTC)
    endpoints: dict[uuid.UUID, list[WebhookEndpoint]] = {}
    with session.no_autoflush:
        for appointment, previous in changes:
            org_id = appointment.organization_id
            if org_id not in endpoints:
                org = session.get(Organization, org_id)
                endpoints[org_id] = (
                    []
                    if org is None or org.is_demo
                    else list(
                        session.scalars(
                            select(WebhookEndpoint).where(
                                WebhookEndpoint.organization_id == org_id,
                                WebhookEndpoint.enabled.is_(True),
                            )
                        )
                    )
                )
            for endpoint in endpoints[org_id]:
                delivery_id = uuid.uuid7()
                session.add(
                    WebhookDelivery(
                        id=delivery_id,
                        organization_id=org_id,
                        endpoint_id=endpoint.id,
                        event=EVENT_STATUS_CHANGED,
                        payload=_payload(delivery_id, appointment, previous, now),
                        status=DeliveryStatus.PENDING,
                        created_at=now,
                        next_attempt_at=now,
                    )
                )


# Registered for every session: no code path can change a status without the outbox seeing it.
event.listen(Session, "before_flush", _enqueue)


# Delivery --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    delivered: int
    retrying: int
    failed: int


def _attempt(
    client: httpx2.Client, endpoint: WebhookEndpoint, delivery: WebhookDelivery, now: dt.datetime
) -> tuple[int | None, str | None]:
    body = json.dumps(delivery.payload, separators=(",", ":"), sort_keys=True).encode()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "PrefixAI-Webhooks/1",
        "X-Prefix-Event": delivery.event,
        "X-Prefix-Delivery": str(delivery.id),
        "X-Prefix-Signature": signature(endpoint.secret, int(now.timestamp()), body),
    }
    try:
        response = client.post(endpoint.url, content=body, headers=headers)
    except httpx2.HTTPError as exc:
        return None, type(exc).__name__
    if 200 <= response.status_code < 300:
        return response.status_code, None
    return response.status_code, f"HTTP {response.status_code}"


def deliver_due(
    session_factory: SessionFactory,
    *,
    now: dt.datetime,
    client: httpx2.Client | None = None,
    batch_size: int = 50,
) -> DeliveryResult:
    """Post every due delivery once. Rows are locked, so two workers never send the same one."""
    http = client or httpx2.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False)
    delivered = retrying = failed = 0
    with session_factory() as session, session.begin():
        due = session.execute(
            select(WebhookDelivery, WebhookEndpoint)
            .join(WebhookEndpoint, WebhookEndpoint.id == WebhookDelivery.endpoint_id)
            .where(
                WebhookDelivery.status == DeliveryStatus.PENDING,
                WebhookDelivery.next_attempt_at <= now,
            )
            .order_by(WebhookDelivery.next_attempt_at)
            .limit(batch_size)
            .with_for_update(of=WebhookDelivery, skip_locked=True)
        ).all()
        for delivery, endpoint in due:
            if not endpoint.enabled:
                delivery.status = DeliveryStatus.FAILED
                delivery.last_error = "Endpoint disabled"
                failed += 1
                continue
            code, error = _attempt(http, endpoint, delivery, now)
            delivery.attempts += 1
            delivery.last_status_code = code
            delivery.last_error = error
            if error is None:
                delivery.status = DeliveryStatus.DELIVERED
                delivery.delivered_at = now
                delivered += 1
            elif delivery.attempts >= MAX_ATTEMPTS:
                delivery.status = DeliveryStatus.FAILED
                failed += 1
            else:
                delivery.next_attempt_at = now + BACKOFF[delivery.attempts - 1]
                retrying += 1
    return DeliveryResult(delivered=delivered, retrying=retrying, failed=failed)
