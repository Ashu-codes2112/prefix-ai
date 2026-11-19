"""Messaging status for the staff app, and the admin's test message. No patient data."""

import dataclasses
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from prefix_api.api.deps import (
    DbSession,
    MessagingProviderDep,
    Now,
    SettingsDep,
    StaffUser,
    require,
)
from prefix_api.domain.enums import MessageChannel
from prefix_api.domain.permissions import Permission
from prefix_api.domain.principal import StaffPrincipal
from prefix_api.schemas.messaging import MessagingStatusOut, MessagingTestIn, MessagingTestOut
from prefix_api.services.messaging_status import MessagingService

router = APIRouter(prefix="/messaging", tags=["messaging"])

Tester = Annotated[StaffPrincipal, Depends(require(Permission.MESSAGING_TEST))]


@router.get("/status")
def messaging_status(
    principal: StaffUser, session: DbSession, settings: SettingsDep, provider: MessagingProviderDep
) -> MessagingStatusOut:
    status = MessagingService(session, settings, provider).status(principal)
    return MessagingStatusOut(**dataclasses.asdict(status))


@router.post("/test")
def send_test_message(
    body: MessagingTestIn,
    principal: Tester,
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    provider: MessagingProviderDep,
    now: Now,
) -> MessagingTestOut:
    """Admins only: one test message to the number given, a few times an hour at most."""
    result = MessagingService(session, settings, provider).send_test(
        principal,
        to=body.phone,
        channel=MessageChannel(body.channel),
        limiter=request.app.state.rate_limiter,
        now=now,
    )
    return MessagingTestOut(**dataclasses.asdict(result))
