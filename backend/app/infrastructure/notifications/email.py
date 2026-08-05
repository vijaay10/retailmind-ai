"""Email delivery.

A port with three implementations, and the choice of which is wired is a
configuration decision rather than a code one — which is what keeps a test
suite from ever reaching a real mail server.

``SmtpEmailSender``
    The production path. Uses TLS, and fails loudly rather than silently: a
    notification system that swallows delivery errors reports perfect health
    while nobody receives anything.

``RecordingEmailSender``
    Captures messages in memory. What the tests assert against.

``NullEmailSender``
    Logs and discards. For local development, where a real send would be an
    accident waiting to reach a customer's inbox from somebody's laptop.
"""

import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol

import structlog

log = structlog.get_logger(__name__)

#: Subject lines longer than this get truncated by most clients anyway, and a
#: subject that runs off the end hides the part that says what happened.
MAX_SUBJECT = 120


@dataclass(frozen=True, slots=True)
class Message:
    """One email, already rendered."""

    to: str
    subject: str
    body: str
    html: str = ""


class EmailSender(Protocol):
    """What every delivery implementation provides."""

    def send(self, message: Message) -> None: ...


@dataclass
class RecordingEmailSender:
    """Captures messages instead of sending them."""

    sent: list[Message] = field(default_factory=list)
    fail_on: str = ""
    """Address that raises on send, so retry and failure paths are testable."""

    def send(self, message: Message) -> None:
        if self.fail_on and message.to == self.fail_on:
            raise smtplib.SMTPException(f"simulated failure for {message.to}")
        self.sent.append(message)


@dataclass(frozen=True, slots=True)
class NullEmailSender:
    """Logs the intent and delivers nothing."""

    def send(self, message: Message) -> None:
        log.info("email.suppressed", to=message.to, subject=message.subject[:MAX_SUBJECT])


@dataclass(frozen=True, slots=True)
class SmtpEmailSender:
    """Sends over SMTP with STARTTLS."""

    host: str
    port: int = 587
    username: str = ""
    password: str = ""
    sender: str = "alerts@retailmind.local"
    timeout: float = 10.0

    def send(self, message: Message) -> None:
        payload = EmailMessage()
        payload["From"] = self.sender
        payload["To"] = message.to
        payload["Subject"] = message.subject[:MAX_SUBJECT]
        payload.set_content(message.body)
        if message.html:
            payload.add_alternative(message.html, subtype="html")

        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as client:
            client.starttls()
            if self.username:
                client.login(self.username, self.password)
            client.send_message(payload)

        log.info("email.sent", to=message.to, subject=message.subject[:MAX_SUBJECT])


def render(candidate_payload: dict[str, object]) -> Message:
    """Render a notification payload into an email.

    Plain text carries the whole message; the HTML alternative is a
    convenience. A recipient reading on a locked-down client or a watch gets
    the same information, which for an alert is the point.
    """
    title = str(candidate_payload.get("title", "RetailMind alert"))
    body = str(candidate_payload.get("body", ""))
    severity = str(candidate_payload.get("severity", "info")).upper()
    link = str(candidate_payload.get("deep_link", ""))

    text = f"[{severity}] {title}\n\n{body}\n"
    if link:
        text += f"\nOpen: {link}\n"

    return Message(
        to="",  # filled in per recipient by the fan-out
        subject=f"[{severity}] {title}",
        body=text,
        html=(
            f"<p><strong>[{_escape(severity)}]</strong> {_escape(title)}</p>"
            f"<p>{_escape(body)}</p>"
            + (f'<p><a href="{_escape(link)}">Open in RetailMind</a></p>' if link else "")
        ),
    )


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
