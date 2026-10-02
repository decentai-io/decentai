"""server/authentication/mail.py — delivery over SMTP, and the log when
there is no server to deliver through."""

from types import SimpleNamespace

import pytest

from server.authentication import mail


class FakeServer:
    """What the mailer does with a connection, written down."""

    opened = []

    def __init__(self, host, port, timeout=None, context=None, ssl=False):
        self.host, self.port, self.ssl = host, port, ssl
        self.calls = []
        self.sent = []
        FakeServer.opened.append(self)

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, username, password):
        self.calls.append(("login", username, password))

    def send_message(self, message):
        self.sent.append(message)

    def quit(self):
        self.calls.append("quit")


class FakeSSLServer(FakeServer):
    def __init__(self, host, port, timeout=None, context=None):
        super().__init__(host, port, timeout, context, ssl=True)


class RefusingServer(FakeServer):
    def send_message(self, message):
        raise OSError("relay access denied")


class WrongPasswordServer(FakeServer):
    def login(self, username, password):
        raise OSError("authentication failed")

    def close(self):
        self.calls.append("close")


@pytest.fixture()
def smtp(monkeypatch):
    FakeServer.opened = []
    monkeypatch.setattr(mail.smtplib, "SMTP", FakeServer)
    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", FakeSSLServer)
    return FakeServer


def settings(**values):
    base = {"smtp_host": "smtp.example.test", "mail_from": "no-reply@example.test",
            "smtp_port": 0, "smtp_username": "", "smtp_password": "",
            "smtp_security": "starttls"}
    base.update(values)
    return SimpleNamespace(**base)


def send(mailer):
    return mailer.send("dana@example.test", "Join the workspace",
                       "<p>Open <a href='https://x.test/j'>this</a></p>",
                       "Open https://x.test/j")


class TestWhenThereIsNoServer:
    def test_nothing_is_sent_and_the_message_is_logged(self, smtp):
        result = send(mail.Mailer(settings(smtp_host="")))
        assert (result.delivered, result.logged) == (False, True)
        assert smtp.opened == []

    def test_a_sender_is_needed_too(self, smtp):
        assert not mail.Mailer(settings(mail_from="")).configured


class TestDelivery:
    def test_starttls_on_587_by_default_and_a_message_with_both_parts(self, smtp):
        result = send(mail.Mailer(settings()))
        assert result.delivered and not result.logged
        server = smtp.opened[0]
        assert (server.host, server.port, server.ssl) == ("smtp.example.test", 587, False)
        assert server.calls == ["starttls", "quit"]
        message = server.sent[0]
        assert message["From"] == "no-reply@example.test"
        assert message["To"] == "dana@example.test"
        assert message["Subject"] == "Join the workspace"
        assert message["Message-ID"]
        kinds = [part.get_content_type() for part in message.iter_parts()]
        assert kinds == ["text/plain", "text/html"]

    def test_ssl_opens_on_465(self, smtp):
        send(mail.Mailer(settings(smtp_security="ssl")))
        server = smtp.opened[0]
        assert (server.port, server.ssl) == (465, True)
        assert "starttls" not in server.calls

    def test_none_is_a_plain_relay(self, smtp):
        send(mail.Mailer(settings(smtp_security="none", smtp_port=25)))
        server = smtp.opened[0]
        assert server.port == 25 and server.calls == ["quit"]

    def test_it_signs_in_when_given_a_username(self, smtp):
        send(mail.Mailer(settings(smtp_username="apikey", smtp_password="s3cret")))
        assert ("login", "apikey", "s3cret") in smtp.opened[0].calls

    def test_an_unknown_security_is_starttls(self, smtp):
        send(mail.Mailer(settings(smtp_security="tls-maybe")))
        assert smtp.opened[0].calls[0] == "starttls"

    def test_a_refusal_is_reported_not_raised(self, monkeypatch):
        monkeypatch.setattr(mail.smtplib, "SMTP", RefusingServer)
        result = send(mail.Mailer(settings()))
        assert (result.delivered, result.logged) == (False, False)
        assert "relay access denied" in result.detail

    def test_a_failed_sign_in_closes_the_connection(self, monkeypatch):
        FakeServer.opened = []
        monkeypatch.setattr(mail.smtplib, "SMTP", WrongPasswordServer)
        result = send(mail.Mailer(settings(smtp_username="apikey")))
        assert "authentication failed" in result.detail
        assert FakeServer.opened[0].calls == ["starttls", "close"]

    def test_a_header_the_library_refuses_is_reported_not_raised(self, smtp):
        result = mail.Mailer(settings()).send(
            "dana@example.test", "Two\nlines", "<p>Hi</p>", "Hi")
        assert (result.delivered, result.logged) == (False, False)
        assert smtp.opened == []
