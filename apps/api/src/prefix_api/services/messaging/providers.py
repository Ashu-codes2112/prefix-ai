"""Concrete messaging providers and the factory that picks them from settings."""

import base64
import hashlib
import hmac
import smtplib
import uuid
from email.message import EmailMessage

import httpx2

from prefix_api.core.config import Settings
from prefix_api.core.logging import get_logger
from prefix_api.domain.enums import MessageChannel
from prefix_api.services.messaging.base import (
    MessagingError,
    MessagingProvider,
    OutboundMessage,
    SendResult,
    mask_recipient,
)

logger = get_logger(__name__)

TWILIO_API = "https://api.twilio.com/2010-04-01"


class ConsoleProvider:
    """Development default: logs that a message would be sent. The body is not logged."""

    name = "console"

    def send(self, message: OutboundMessage) -> SendResult:
        logger.info(
            "message.console_send",
            channel=message.channel.value,
            to=mask_recipient(message.to),
            characters=len(message.body),
        )
        return SendResult(provider=self.name, provider_message_id=f"console-{uuid.uuid4().hex}")


class TwilioMessages:
    """Twilio's Messages API (one POST per message), shared by the SMS and WhatsApp providers."""

    def __init__(
        self, *, account_sid: str, auth_token: str, client: httpx2.Client | None = None
    ) -> None:
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._client = client or httpx2.Client(timeout=15.0)

    def post(self, *, sender: str, recipient: str, body: str) -> SendResult:
        try:
            response = self._client.post(
                f"{TWILIO_API}/Accounts/{self._account_sid}/Messages.json",
                data={"From": sender, "To": recipient, "Body": body},
                auth=(self._account_sid, self._auth_token),
            )
        except httpx2.HTTPError as exc:
            raise MessagingError(f"Twilio request failed: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise MessagingError(f"Twilio rejected the message (HTTP {response.status_code})")
        sid = response.json().get("sid")
        return SendResult(provider="twilio", provider_message_id=str(sid) if sid else None)


class TwilioSMSProvider:
    """SMS from TWILIO_FROM_NUMBER."""

    name = "twilio"

    def __init__(self, api: TwilioMessages, *, sender: str | None) -> None:
        self._api = api
        self._sender = sender

    def send(self, message: OutboundMessage) -> SendResult:
        if not self._sender:
            raise MessagingError("TWILIO_FROM_NUMBER is not configured")
        return self._api.post(sender=self._sender, recipient=message.to, body=message.body)


class TwilioWhatsAppProvider:
    """WhatsApp from TWILIO_WHATSAPP_FROM (or the SMS number). Works with Twilio's sandbox,
    where the sender is +14155238886 and each recipient first joins the sandbox."""

    name = "twilio"

    def __init__(self, api: TwilioMessages, *, sender: str | None) -> None:
        self._api = api
        self._sender = sender

    def send(self, message: OutboundMessage) -> SendResult:
        if not self._sender:
            raise MessagingError("TWILIO_WHATSAPP_FROM is not configured")
        return self._api.post(
            sender=f"whatsapp:{self._sender.removeprefix('whatsapp:')}",
            recipient=f"whatsapp:{message.to.removeprefix('whatsapp:')}",
            body=message.body,
        )


class SmtpProvider:
    name = "smtp"

    def __init__(
        self,
        *,
        host: str,
        port: int,
        sender: str,
        username: str | None,
        password: str | None,
        starttls: bool,
    ) -> None:
        self._host = host
        self._port = port
        self._sender = sender
        self._username = username
        self._password = password
        self._starttls = starttls

    def send(self, message: OutboundMessage) -> SendResult:
        email = EmailMessage()
        email["From"] = self._sender
        email["To"] = message.to
        email["Subject"] = message.subject
        message_id = f"<{uuid.uuid4().hex}@prefix-ai>"
        email["Message-ID"] = message_id
        email.set_content(message.body)
        try:
            with smtplib.SMTP(self._host, self._port, timeout=15) as smtp:
                if self._starttls:
                    smtp.starttls()
                if self._username and self._password:
                    smtp.login(self._username, self._password)
                smtp.send_message(email)
        except (smtplib.SMTPException, OSError) as exc:
            raise MessagingError(f"SMTP send failed: {type(exc).__name__}") from exc
        return SendResult(provider=self.name, provider_message_id=message_id)


class UnavailableProvider:
    def __init__(self, reason: str) -> None:
        self._reason = reason

    def send(self, message: OutboundMessage) -> SendResult:
        raise MessagingError(self._reason)


class ChannelRouter:
    """Sends each message through the provider configured for its channel."""

    def __init__(self, providers: dict[MessageChannel, MessagingProvider]) -> None:
        self._providers = providers

    def send(self, message: OutboundMessage) -> SendResult:
        provider = self._providers.get(message.channel)
        if provider is None:
            raise MessagingError(f"No provider for channel {message.channel.value}")
        return provider.send(message)


def build_messaging_provider(settings: Settings) -> MessagingProvider:
    """Twilio for SMS and WhatsApp (and SMTP for email) when messaging is live; otherwise the
    console, which logs that a message would go out ("demo mode")."""
    if not settings.messaging_live:
        return ConsoleProvider()

    api = TwilioMessages(
        account_sid=settings.twilio_account_sid or "",
        auth_token=settings.twilio_auth_token.get_secret_value()
        if settings.twilio_auth_token
        else "",
    )
    email: MessagingProvider
    if settings.smtp_host and settings.smtp_from:
        email = SmtpProvider(
            host=settings.smtp_host,
            port=settings.smtp_port,
            sender=settings.smtp_from,
            username=settings.smtp_username,
            password=settings.smtp_password.get_secret_value() if settings.smtp_password else None,
            starttls=settings.smtp_starttls,
        )
    else:
        email = UnavailableProvider("SMTP is not configured")
    return ChannelRouter(
        {
            MessageChannel.SMS: TwilioSMSProvider(api, sender=settings.sms_sender),
            MessageChannel.WHATSAPP: TwilioWhatsAppProvider(api, sender=settings.whatsapp_sender),
            MessageChannel.EMAIL: email,
        }
    )


def twilio_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    """Twilio's X-Twilio-Signature: base64(HMAC-SHA1(token, url + sorted key/value pairs))."""
    payload = url + "".join(f"{key}{params[key]}" for key in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def verify_twilio_signature(
    auth_token: str, url: str, params: dict[str, str], signature: str
) -> bool:
    return hmac.compare_digest(twilio_signature(auth_token, url, params), signature)
