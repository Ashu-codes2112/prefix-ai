"""Whether patient messages really go out, and the admin's one-off test message.

"Demo mode" means the console provider: messages are logged with the recipient masked and never
sent. The Demo Hospital is always in demo mode for its patients (see ``dispatch``). The single
exception is the admin's test message: it goes through the real provider, to one number the
admin types, a few times an hour at most, and every attempt is audited.
"""

import datetime as dt
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from prefix_api.core.config import Settings
from prefix_api.core.rate_limit import RateLimiter
from prefix_api.db.models import Organization
from prefix_api.domain.enums import AuditAction, MessageChannel
from prefix_api.domain.permissions import Permission
from prefix_api.domain.principal import StaffPrincipal
from prefix_api.services.audit import record_audit
from prefix_api.services.messaging.base import (
    MessagingError,
    MessagingProvider,
    OutboundMessage,
    mask_recipient,
)

MessagingMode = Literal["live", "demo"]
TestOutcome = Literal["sent", "logged", "failed"]

TEST_BODY = (
    "Prefix AI test message: this is how prep reminders will reach your patients. "
    "No reply is needed."
)
TESTS_PER_WINDOW = 3
TEST_WINDOW_SECONDS = 3600.0
PURPOSE = "messaging_test"


@dataclass(frozen=True, slots=True)
class MessagingStatus:
    mode: MessagingMode
    # Masked, e.g. "***111"; None when that channel has no sender.
    sms_sender: str | None
    whatsapp_sender: str | None
    # The Demo Hospital's own patients never get real messages, whatever the mode.
    demo_hospital: bool
    can_send_test: bool


@dataclass(frozen=True, slots=True)
class TestResult:
    outcome: TestOutcome
    mode: MessagingMode
    detail: str


def _masked(number: str | None) -> str | None:
    return mask_recipient(number) if number else None


class MessagingService:
    def __init__(self, session: Session, settings: Settings, provider: MessagingProvider) -> None:
        self._session = session
        self._settings = settings
        self._provider = provider

    @property
    def mode(self) -> MessagingMode:
        return "live" if self._settings.messaging_live else "demo"

    def status(self, principal: StaffPrincipal) -> MessagingStatus:
        """No patient data: the mode, masked sender numbers and the hospital's demo flag."""
        organization = self._session.get(Organization, principal.organization_id)
        return MessagingStatus(
            mode=self.mode,
            sms_sender=_masked(self._settings.sms_sender),
            whatsapp_sender=_masked(self._settings.whatsapp_sender),
            demo_hospital=organization is not None and organization.is_demo,
            can_send_test=principal.can(Permission.MESSAGING_TEST),
        )

    def send_test(
        self,
        principal: StaffPrincipal,
        *,
        to: str,
        channel: MessageChannel,
        limiter: RateLimiter,
        now: dt.datetime,
    ) -> TestResult:
        """One fixed test message to ``to``, through the real provider, even in the Demo
        Hospital. The number is not stored; the audit entry records only the outcome."""
        principal.require(Permission.MESSAGING_TEST)
        limiter.check(
            f"messaging-test:{principal.user_id}",
            limit=TESTS_PER_WINDOW,
            window_seconds=TEST_WINDOW_SECONDS,
        )
        outcome: TestOutcome
        try:
            self._provider.send(OutboundMessage(channel=channel, to=to, body=TEST_BODY))
        except MessagingError as exc:
            outcome, detail = "failed", str(exc)
        else:
            if self.mode == "live":
                outcome, detail = "sent", f"Sent to {mask_recipient(to)} by {channel.value}."
            else:
                outcome = "logged"
                detail = "Demo mode: the message was logged, not sent. Set the Twilio values."
        record_audit(
            self._session,
            principal=principal,
            action=AuditAction.CREATE,
            entity_type="messaging_test",
            purpose=PURPOSE,
            details={"status": outcome},
        )
        return TestResult(outcome=outcome, mode=self.mode, detail=detail)
