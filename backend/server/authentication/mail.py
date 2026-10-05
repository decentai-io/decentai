"""Outbound email. Emails = the templates (HTML + plain text, inline styles
only so every client renders alike). Mailer = delivery over SMTP, the
standard every mail provider speaks, that degrades: with no server
configured it logs the message with its link instead of raising, and
``send`` reports delivered-vs-logged so callers can be honest about it.
"""

from __future__ import annotations

import html
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Dict

from server.custom_logging import CustomLoggerFactory


class Emails:
    BRAND = "DecentAI"

    INK = "#14161A"
    MUTED = "#6b6f76"
    BORDER = "#e4e4e7"
    PAPER = "#F8F7F4"
    ACCENT = "#0E7C5B"          # the dot's green — the brand's single accent

    @staticmethod
    def _mark_url() -> str:
        """The mark, served by the app itself: an email cannot carry a
        file, but it can point at the one address every recipient can
        already reach."""
        from server.setup.app_state import get_settings

        base = str(get_settings().public_app_url or "").rstrip("/")
        return f"{base}/assets/brand/icons/decentai-mark-48.png" if base else ""

    # ------------------------------------------------------------------
    @classmethod
    def _shell(cls, heading: str, body_html: str, footer: str) -> str:
        """One frame for every message, so they all look like the same sender.

        The footer is the caller's because it says WHY this arrived, and
        each message has its own answer. One sentence about invitations
        printed under a password reset tells the reader something untrue
        on exactly the email where provenance matters most.
        """
        return f"""\
            <div style="margin:0;padding:24px;background:{cls.PAPER};
                        font-family:'IBM Plex Sans',-apple-system,BlinkMacSystemFont,
                        'Segoe UI',Helvetica,Arial,sans-serif;color:{cls.INK};">
              <div style="max-width:520px;margin:0 auto;padding:32px;background:#ffffff;
                          border:1px solid {cls.BORDER};border-radius:12px;">
                <div style="display:flex;align-items:center;gap:10px;margin-bottom:24px;">
                  {cls._mark_img()}
                  <span style="font-size:15px;font-weight:600;letter-spacing:-0.01em;
                               color:{cls.INK};">{cls.BRAND}</span>
                </div>
                <h1 style="margin:0 0 16px;font-size:20px;font-weight:600;
                           letter-spacing:-0.01em;color:{cls.INK};">{heading}</h1>
                {body_html}
              </div>
              <div style="max-width:520px;margin:16px auto 0;font-size:12px;
                          color:{cls.MUTED};text-align:center;">
                {footer}
              </div>
            </div>
        """

    @classmethod
    def _mark_img(cls) -> str:
        url = cls._mark_url()
        if not url:
            return ""
        return (f'<img src="{html.escape(url, quote=True)}" width="24" height="24" '
                f'alt="" style="display:block;width:24px;height:24px;">')

    @classmethod
    def _button(cls, url: str, label: str) -> str:
        return (
            f'<a href="{html.escape(url, quote=True)}" '
            f'style="display:inline-block;padding:11px 20px;margin:8px 0 20px;'
            f'font-size:15px;font-weight:500;color:#ffffff;background:{cls.ACCENT};'
            f'border-radius:8px;text-decoration:none;">{html.escape(label)}</a>'
        )

    @classmethod
    def _paragraph(cls, text: str, muted: bool = False) -> str:
        color = cls.MUTED if muted else cls.INK
        size = "13px" if muted else "15px"
        return (
            f'<p style="margin:0 0 16px;font-size:{size};line-height:1.55;'
            f'color:{color};">{text}</p>'
        )

    # ------------------------------------------------------------------
    @classmethod
    def invitation(
        cls,
        *,
        organization_name: str,
        inviter_name: str,
        accept_url: str,
        expires_in_days: int,
    ) -> Dict[str, str]:
        """Invite someone into an organization."""
        # Two forms of the same names, and they are not interchangeable:
        # the escaped ones are for HTML, the plain ones for the subject
        # line and the text part. Reusing an escaped name there is what
        # turns O'Brien into O&#x27;Brien in somebody's inbox.
        inviter_plain = inviter_name or "Someone"
        org = html.escape(organization_name)
        inviter = html.escape(inviter_plain)

        body = "".join([
            cls._paragraph(
                f"<strong>{inviter}</strong> invited you to join "
                f"<strong>{org}</strong> on {cls.BRAND}."
            ),
            cls._button(accept_url, "Accept the invitation"),
            cls._paragraph(
                f"The link expires in {expires_in_days} days. If it does, ask "
                f"{inviter} to send a new one.",
                muted=True,
            ),  # HTML — the escaped name belongs here
            cls._paragraph(
                "If you weren't expecting this invitation you can ignore this "
                "email — nothing is created until you accept it.",
                muted=True,
            ),
            # Some clients strip links; the raw URL is the escape hatch.
            f'<p style="margin:24px 0 0;padding-top:16px;font-size:12px;'
            f'line-height:1.5;color:{cls.MUTED};border-top:1px solid {cls.BORDER};'
            f'word-break:break-all;">If the button does not work, paste this '
            f'into your browser:<br>{html.escape(accept_url)}</p>',
        ])

        text = (
            f"{inviter_plain} invited you to join {organization_name} on "
            f"{cls.BRAND}.\n\n"
            f"Accept the invitation:\n{accept_url}\n\n"
            f"The link expires in {expires_in_days} days.\n\n"
            "If you weren't expecting this invitation you can ignore this "
            "email — nothing is created until you accept it.\n"
        )

        return {
            "subject": (
                f"{inviter_plain} invited you to {organization_name} "
                f"on {cls.BRAND}"
            ),
            "html": cls._shell(
                f"Join {org}",
                body,
                f"You received this because someone invited you to a "
                f"{cls.BRAND} workspace.",
            ),
            "text": text,
        }

    @classmethod
    def notification(
        cls,
        *,
        title: str,
        body: str,
        url: str,
        chat_title: str,
    ) -> Dict[str, str]:
        """A chat needs the person, or answered them, while they were
        away and no device could be pushed to."""
        heading = html.escape(title)
        body_html = "".join([
            cls._paragraph(html.escape(body)) if body else "",
            cls._paragraph(f"In your chat <strong>{html.escape(chat_title)}</strong>.", muted=True),
            cls._button(url, "Open the chat"),
        ])
        text = f"{title}\n\n{body}\n\nIn your chat \"{chat_title}\".\nOpen it: {url}\n"
        return {
            "subject": f"{cls.BRAND}: {title}",
            "html": cls._shell(
                heading, body_html,
                f"You received this because a chat of yours on {cls.BRAND} needed you "
                f"while no device of yours could be reached. Turn email off under "
                f"Settings, Chat configuration."),
            "text": text,
        }

    @classmethod
    def password_reset(
        cls,
        *,
        organization_name: str,
        reset_url: str,
        expires_in_minutes: int,
    ) -> Dict[str, str]:
        """Set a new password on an existing account, naming the
        organization the account is in."""
        org = html.escape(organization_name)

        body = "".join([
            cls._paragraph(
                f"Someone asked to reset the password for your "
                f"<strong>{org}</strong> account on {cls.BRAND}."
            ),
            cls._button(reset_url, "Choose a new password"),
            cls._paragraph(
                f"The link works once and expires in {expires_in_minutes} "
                "minutes.",
                muted=True,
            ),
            # The reassurance that matters: ignoring this is safe and complete.
            cls._paragraph(
                "If this wasn't you, ignore this email — your password stays "
                "as it is, and whoever asked cannot see it.",
                muted=True,
            ),
            f'<p style="margin:24px 0 0;padding-top:16px;font-size:12px;'
            f'line-height:1.5;color:{cls.MUTED};border-top:1px solid {cls.BORDER};'
            f'word-break:break-all;">If the button does not work, paste this '
            f'into your browser:<br>{html.escape(reset_url)}</p>',
        ])

        text = (
            f"Someone asked to reset the password for your {organization_name} "
            f"account on {cls.BRAND}.\n\n"
            f"Choose a new password:\n{reset_url}\n\n"
            f"The link works once and expires in {expires_in_minutes} minutes.\n\n"
            "If this wasn't you, ignore this email — your password stays as it "
            "is, and whoever asked cannot see it.\n"
        )

        return {
            "subject": f"Reset your {organization_name} password",
            "html": cls._shell(
                "Reset your password",
                body,
                f"You received this because a password reset was requested "
                f"for your {cls.BRAND} account.",
            ),
            "text": text,
        }


@dataclass(frozen=True)
class SendResult:
    """What actually happened, so callers can be truthful about it."""

    delivered: bool          # the mail server accepted the message
    logged: bool             # written to the log instead (no server configured)
    detail: str = ""         # message id, or why it failed

    @property
    def ok(self) -> bool:
        return self.delivered or self.logged


class Mailer:
    """Sends through the deployment's SMTP server.

    ``SMTP_HOST`` and ``MAIL_FROM`` are the switch: without both nothing is
    sent, and each message is logged instead — what local development and
    an install on one's own computer, which have no mail server, want. ``SMTP_SECURITY``
    says how the connection is protected: ``starttls`` (port 587, the
    default), ``ssl`` (port 465), or ``none`` for a relay on a trusted
    network."""

    SECURITY = ("starttls", "ssl", "none")
    TIMEOUT_SECONDS = 20

    def __init__(self, settings):
        self.settings = settings
        self.host = (getattr(settings, "smtp_host", "") or "").strip()
        self.sender = (getattr(settings, "mail_from", "") or "").strip()
        self.username = (getattr(settings, "smtp_username", "") or "").strip()
        self.password = getattr(settings, "smtp_password", "") or ""
        security = (getattr(settings, "smtp_security", "") or "starttls").strip().lower()
        self.security = security if security in self.SECURITY else "starttls"
        default_port = 465 if self.security == "ssl" else 587
        self.port = int(getattr(settings, "smtp_port", 0) or default_port)
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    # ------------------------------------------------------------------
    @property
    def configured(self) -> bool:
        return bool(self.host and self.sender)

    def _connection(self) -> smtplib.SMTP:
        """An open, protected and signed-in connection to the server."""
        context = ssl.create_default_context()
        if self.security == "ssl":
            server = smtplib.SMTP_SSL(self.host, self.port,
                                      timeout=self.TIMEOUT_SECONDS, context=context)
        else:
            server = smtplib.SMTP(self.host, self.port, timeout=self.TIMEOUT_SECONDS)
        try:
            if self.security == "starttls":
                server.starttls(context=context)
            if self.username:
                server.login(self.username, self.password)
        except Exception:
            server.close()
            raise
        return server

    def _message(self, to_address: str, subject: str, html_body: str,
                 text_body: str) -> EmailMessage:
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = to_address
        message["Subject"] = subject
        message["Date"] = formatdate(localtime=False)
        message["Message-ID"] = make_msgid(domain=self.sender.rpartition("@")[2] or None)
        message.set_content(text_body)
        message.add_alternative(html_body, subtype="html")
        return message

    # ------------------------------------------------------------------
    def send(
        self,
        to_address: str,
        subject: str,
        html_body: str,
        text_body: str,
    ) -> SendResult:
        to_address = (to_address or "").strip()
        if not to_address:
            return SendResult(False, False, "No recipient address.")

        if not self.configured:
            # The text part carries the link in plain sight, which is exactly
            # what a developer needs here.
            self.logger.info(
                "Email is not configured (SMTP_HOST and MAIL_FROM are unset) — "
                f"not sent. To: {to_address}\nSubject: {subject}\n\n{text_body}"
            )
            return SendResult(False, True, "Logged: email is not configured.")

        try:
            # Built inside: a header the mail library refuses (a newline
            # in a name) is a message not sent, not a failed request.
            message = self._message(to_address, subject, html_body, text_body)
            server = self._connection()
            try:
                server.send_message(message)
            finally:
                server.quit()
            message_id = str(message["Message-ID"])
            self.logger.info(f"Sent '{subject}' to {to_address} ({message_id})")
            return SendResult(True, False, message_id)

        except Exception as exc:
            # Delivery is not the caller's transaction — report and move on.
            self.logger.error(
                f"Email to {to_address} was not sent: {exc}", exc_info=True
            )
            return SendResult(False, False, str(exc))
