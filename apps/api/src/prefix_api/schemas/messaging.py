from typing import Literal

from pydantic import BaseModel, ConfigDict

from prefix_api.schemas.patients import E164Phone
from prefix_api.services.messaging_status import MessagingMode, TestOutcome


class MessagingStatusOut(BaseModel):
    mode: MessagingMode
    sms_sender: str | None
    whatsapp_sender: str | None
    demo_hospital: bool
    can_send_test: bool


class MessagingTestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone: E164Phone
    channel: Literal["SMS", "WHATSAPP"] = "SMS"


class MessagingTestOut(BaseModel):
    outcome: TestOutcome
    mode: MessagingMode
    detail: str
