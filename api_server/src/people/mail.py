"""Mail, by the operator's SMTP server, in one template: a few lines and a link (api-server.md §4)."""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from ..utils import Settings

log = logging.getLogger("api_server")


class Mailer:
    @property
    def ready(self) -> bool:
        return Settings.read().mail

    def send(self, to: str, subject: str, lines: list[str], link: str = "") -> None:
        """Sent at once; a header can't carry a line break, so nobody's words add one."""
        settings = Settings.read()
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = settings.mail_from, to, " ".join(subject.split())[:200]
        message.set_content("\n\n".join([*lines, *([link] if link else []), "— DecentAI"]))
        context = ssl.create_default_context()
        if settings.smtp_security == "tls":
            server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20, context=context)
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20)
        with server:
            if settings.smtp_security == "starttls":
                server.starttls(context=context)
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(message)
        log.info(f"Mail sent: {subject[:60]}")
