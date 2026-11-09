"""What every part uses and none owns: the server's settings, and its one way of calling out
(documentation/api-server.md §9, §11)."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any, Callable, Iterator
from urllib.parse import urljoin, urlsplit

import httpx

KIB, MIB, GIB = 1024, 1024 ** 2, 1024 ** 3


class SettingsRefused(ValueError):
    """A setting that can't be used: what to set."""


class Settings:
    """Every AI_API_* variable, read once from the environment."""

    _read: Settings | None = None

    def __init__(self, environment: dict[str, str]):
        self.environment = environment
        self.address = self._text("AI_API_ADDRESS", "0.0.0.0:8000")
        self.runtimes_port = self._number("AI_API_RUNTIMES_PORT", 8004, 1, 65535)
        self.identity = self._text("AI_API_IDENTITY", "/run/decentai/api")
        self.proxies = self._list("AI_API_PROXIES", "127.0.0.1")
        self.url = self._required("AI_API_URL").rstrip("/")
        self.app_url = self._required("AI_API_APP_URL").rstrip("/")
        self.origins = self._list("AI_API_ORIGINS", self.app_url)
        self.database_uri = self._required("AI_API_DATABASE_URI")
        self.database_name = self._text("AI_API_DATABASE_NAME", "decentai")
        self.encryption_keys = self._required("AI_API_ENCRYPTION_KEYS")
        self.encryption_active = self._required("AI_API_ENCRYPTION_ACTIVE")
        self.storage = self._choice("AI_API_STORAGE", "local", ("local", "s3"))
        self.storage_folder = self._text("AI_API_STORAGE_FOLDER", "/data/storage")
        self.s3 = {name: self._text(f"AI_API_S3_{name.upper()}")
                   for name in ("bucket", "region", "endpoint", "prefix", "access_key_id", "secret_access_key",
                                "kms_key_id")}
        self.smtp_host = self._text("AI_API_SMTP_HOST")
        self.smtp_port = self._number("AI_API_SMTP_PORT", 587, 1, 65535)
        self.smtp_username = self._text("AI_API_SMTP_USERNAME")
        self.smtp_password = self._text("AI_API_SMTP_PASSWORD")
        self.smtp_security = self._choice("AI_API_SMTP_SECURITY", "starttls", ("starttls", "tls", "none"))
        self.mail_from = self._text("AI_API_MAIL_FROM")
        self.vapid_public_key = self._text("AI_API_VAPID_PUBLIC_KEY")
        self.vapid_private_key = self._text("AI_API_VAPID_PRIVATE_KEY")
        self.vapid_subject = self._text("AI_API_VAPID_SUBJECT")
        self.speech_url = self._text("AI_API_SPEECH_URL", "http://speech:8004").rstrip("/")
        self.signup = self._choice("AI_API_SIGNUP", "closed", ("closed", "open"))
        self.setup_code = self._text("AI_API_SETUP_CODE")
        self.operator_org = self._text("AI_API_OPERATOR_ORG")
        self.reference_catalog = self._text("AI_API_REFERENCE_CATALOG")
        self.log_level = self._choice("AI_API_LOG_LEVEL", "INFO", ("DEBUG", "INFO", "WARNING", "ERROR"))
        self.log_format = self._choice("AI_API_LOG_FORMAT", "text", ("text", "json"))
        self.inside_hosts = self._list("AI_API_INSIDE_HOSTS")
        self.call_max = self._number("AI_API_CALL_MAX_KIB", 1024, 16, 65536) * KIB
        self.upload_max = self._number("AI_API_UPLOAD_MAX_MIB", 25, 1, 5000) * MIB
        self.calls_per_minute = self._number("AI_API_CALLS_PER_MINUTE", 600, 10, 100000)
        self.outside_per_minute = self._number("AI_API_OUTSIDE_PER_MINUTE", 10, 1, 10000)
        self.files_per_person = self._number("AI_API_FILES_PER_PERSON_GIB", 10, 1, 100000) * GIB
        self.schedules_per_person = self._number("AI_API_SCHEDULES_PER_PERSON", 200, 1, 100000)
        self.schedules_per_org = self._number("AI_API_SCHEDULES_PER_ORG", 2000, 1, 1000000)
        self.kept_days = self._number("AI_API_KEPT_DAYS", 400, 1, 36500)

    @classmethod
    def read(cls) -> Settings:
        if cls._read is None:
            cls._read = cls(dict(os.environ))
        return cls._read

    @property
    def mail(self) -> bool:
        return bool(self.smtp_host and self.mail_from)

    def _text(self, name: str, default: str = "") -> str:
        return self.environment.get(name, "").strip() or default

    def _required(self, name: str) -> str:
        value = self._text(name)
        if not value:
            raise SettingsRefused(f"{name} is required.")
        return value

    def _list(self, name: str, default: str = "") -> list[str]:
        return list(dict.fromkeys(self._text(name, default).replace(",", " ").split()))

    def _choice(self, name: str, default: str, choices: tuple[str, ...]) -> str:
        value = self._text(name, default)
        value = value.upper() if default.isupper() else value.lower()
        if value not in choices:
            raise SettingsRefused(f"{name} is one of: {', '.join(choices)}.")
        return value

    def _number(self, name: str, default: int, low: int, high: int) -> int:
        text = self._text(name, str(default))
        if not text.isdigit() or not low <= int(text) <= high:
            raise SettingsRefused(f"{name}: '{text}' is not a whole number from {low} to {high}.")
        return int(text)


class OutsideRefused(ValueError):
    """An address that may not be reached, or did not answer: words for the person who gave it."""


class Outside:
    """An address people gave, reached the one way: https, public unless AI_API_INSIDE_HOSTS names it, looked up once
    and connected to as checked, each redirect checked again, a credential sent to its own host alone, and the whole
    call held to a deadline and a size."""

    REDIRECTS = 5
    _pool = ThreadPoolExecutor(16, thread_name_prefix="outside")

    def __init__(self, seconds: float = 20.0, most: int = MIB, credential: dict[str, str] | None = None):
        self.seconds, self.most, self.credential = seconds, most, credential or {}

    @classmethod
    async def run(cls, work: Callable[..., Any], *args: Any) -> Any:
        """Work that calls out, on threads of its own: a slow address never holds what other calls need."""
        return await asyncio.get_running_loop().run_in_executor(cls._pool, work, *args)

    def fetch(self, method: str, url: str, data: bytes | None = None,
              headers: dict[str, str] | None = None) -> httpx.Response:
        """The whole answer, read within the deadline and the size."""
        until = time.monotonic() + self.seconds
        with self._opened(method, url, data, headers, until) as response:
            body = bytearray()
            for piece in response.iter_bytes():
                body += piece
                if len(body) > self.most:
                    raise OutsideRefused(f"{self._host(url)} answered more than {self.most // KIB} KiB.")
                if time.monotonic() > until:
                    raise OutsideRefused(f"{self._host(url)} took too long to answer.")
            return httpx.Response(response.status_code, headers=response.headers, content=bytes(body))

    def download(self, url: str, write: Callable[[bytes], None]) -> None:
        """An answer of 200, handed piece by piece to ``write``."""
        until = time.monotonic() + self.seconds
        with self._opened("GET", url, None, None, until) as response:
            if response.status_code != 200:
                raise OutsideRefused(f"{self._host(url)} answered {response.status_code}.")
            taken = 0
            for piece in response.iter_bytes(MIB):
                taken += len(piece)
                if taken > self.most or time.monotonic() > until:
                    raise OutsideRefused(f"{self._host(url)} sent more, or took longer, than it may.")
                write(piece)

    def post(self, url: str, data: Any = None, headers: dict[str, str] | None = None, timeout: Any = None,
             **_: Any) -> httpx.Response:
        """A post, as pywebpush asks one of its session."""
        answer = self.fetch("POST", url, data if isinstance(data, (bytes, type(None))) else str(data).encode(), headers)
        answer.reason = answer.reason_phrase
        return answer

    @classmethod
    def refusal(cls, url: str) -> str:
        """Why an address may not be reached, or ''."""
        try:
            cls._checked(url)
            return ""
        except OutsideRefused as refused:
            return str(refused)

    @contextmanager
    def _opened(self, method: str, url: str, data: bytes | None, headers: dict[str, str] | None,
                until: float) -> Iterator[httpx.Response]:
        first = self._origin(url)
        try:
            with httpx.Client(follow_redirects=False) as client:
                for _ in range(self.REDIRECTS + 1):
                    host, address = self._checked(url)
                    named = httpx.URL(url)
                    sent = {**(headers or {}), **(self.credential if self._origin(url) == first else {}),
                            "Host": named.netloc.decode()}
                    request = client.build_request(method, named.copy_with(host=address), content=data, headers=sent,
                                                   extensions={"sni_hostname": host},
                                                   timeout=max(0.1, until - time.monotonic()))
                    response = client.send(request, stream=True)
                    if not response.is_redirect:
                        break
                    url = urljoin(url, response.headers.get("location", ""))
                    response.close()
                else:
                    raise OutsideRefused(f"{self._host(url)} redirects too many times.")
                try:
                    yield response
                finally:
                    response.close()
        except httpx.HTTPError as failure:
            raise OutsideRefused(f"{self._host(url)} could not be reached.") from failure

    @classmethod
    def _checked(cls, url: str) -> tuple[str, str]:
        """(host, the address to connect to) for an address that may be reached."""
        parts = urlsplit(str(url or ""))
        host = (parts.hostname or "").lower()
        if parts.scheme not in ("https", "http") or not host or parts.username or parts.password:
            raise OutsideRefused("An address is https://host/…, with no name or password in it.")
        try:
            found = list(dict.fromkeys(info[4][0] for info in socket.getaddrinfo(host, parts.port or 443)))
        except (OSError, ValueError):
            raise OutsideRefused(f"{host} could not be found.") from None
        inside = cls._inside(host, found)
        if parts.scheme == "http" and not inside:
            raise OutsideRefused("An address outside this deployment is reached over https.")
        if not inside and any(not cls._public(answer) for answer in found):
            raise OutsideRefused(f"{host} is an address inside this network, and only public ones are reached.")
        return host, found[0]

    @staticmethod
    def _inside(host: str, found: list[str]) -> bool:
        """Whether the operator let people's addresses reach this host."""
        for allowed in Settings.read().inside_hosts:
            if host == allowed.lower():
                return True
            try:
                network = ipaddress.ip_network(allowed, strict=False)
            except ValueError:
                continue
            if found and all(ipaddress.ip_address(answer) in network for answer in found):
                return True
        return False

    @staticmethod
    def _public(address: str) -> bool:
        """The public internet only, an IPv4 address inside an IPv6 one judged by what is inside."""
        ip = ipaddress.ip_address(address)
        if ip.version == 6:
            ip = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None) or ip
        return ip.is_global and not ip.is_multicast

    @staticmethod
    def _origin(url: str) -> tuple[str, str, int | None]:
        parts = urlsplit(url)
        return parts.scheme, (parts.hostname or "").lower(), parts.port

    @staticmethod
    def _host(url: str) -> str:
        return urlsplit(str(url or "")).hostname or "That address"
