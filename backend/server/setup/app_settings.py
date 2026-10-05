import os
from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Literal


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "y", "on")


def _env_list(name: str, default: str = "", separator: str = ",") -> List[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(separator) if item.strip()]


#: What a deployment is: the platform served to an organization at an
#: address of its own, or installed on one person's computer.
DEPLOYMENT_KINDS = ("web", "desktop")


def _env_deployment_kind() -> str:
    """DEPLOYMENT_KIND constrained to what the platform knows; an
    unknown value is a web deployment, which is the stricter of the
    two."""
    value = (os.getenv("DEPLOYMENT_KIND") or "web").strip().lower()
    if value not in DEPLOYMENT_KINDS:
        import logging

        logging.getLogger("Settings").warning(
            f"DEPLOYMENT_KIND='{value}' is not web/desktop — using web."
        )
        return "web"
    return value


def _env_samesite() -> str:
    """JWT_COOKIE_SAMESITE constrained to what browsers accept; an
    unknown value falls back to lax rather than breaking logins."""
    value = (os.getenv("JWT_COOKIE_SAMESITE") or "lax").strip().lower()
    if value not in ("lax", "strict", "none"):
        import logging

        logging.getLogger("Settings").warning(
            f"JWT_COOKIE_SAMESITE='{value}' is not lax/strict/none — "
            f"using lax."
        )
        return "lax"
    return value


def _env_int(name: str, default: int, low: int, high: int) -> int:
    """A bounded whole number. An unreadable or out-of-range value falls
    back to the default rather than failing the boot on a typo."""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(low, min(high, value))


def _env_port(name: str, default: int) -> int:
    """A TCP port. An unreadable or out-of-range value falls back to the
    default rather than failing the boot on a typo."""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        port = int(raw)
    except ValueError:
        return default
    return port if 1 <= port <= 65535 else default


def _env_pem(name: str) -> Optional[str]:
    # PEM keys are stored as single-line env values with \n escapes
    # (env_file-safe); turn them back into a real multiline PEM here.
    raw = (os.getenv(name) or "").strip()
    return raw.replace("\\n", "\n") or None


def _load_settings() -> Dict[str, Any]:
    # Everything is read from the process environment. The deployment is what
    # sets it: docker compose (env_file), an ECS task definition, a K8s
    # manifest — or config.env seeded into os.environ for local development.
    return {
        # Where the server binds. Loopback locally; the container image
        # sets 0.0.0.0, because "localhost" inside a container is the
        # container — the proxy reaching it is somewhere else.
        "backend_host": os.getenv("BACKEND_HOST", "127.0.0.1"),
        "backend_port": _env_port("BACKEND_PORT", 8000),
        # Proxy hops whose X-Forwarded-* headers may be believed. Session
        # IPs and the login lockout are counted per client address, so
        # trusting this from anyone would let a spoofed header dodge the
        # lockout. Comma-separated addresses, or "*" inside a private
        # network.
        "forwarded_allow_ips": os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1"),

        "cors_allow_origins": _env_list("CORS_ALLOW_ORIGINS", "http://localhost:4200"),

        # web: served to an organization at an address of its own.
        # desktop: on one person's own computer, as bootstrap/setup.py
        # sets it up. It decides what cannot be the same for both — an
        # app registered for a desktop has no secret to keep.
        "deployment_kind": _env_deployment_kind(),

        # session token signing + cookie
        "token_secret_key": os.getenv("TOKEN_SECRET_KEY"),
        "jwt_cookie_secure": _env_bool("JWT_COOKIE_SECURE", True),
        "jwt_cookie_samesite": _env_samesite(),
        # Empty means "no Domain attribute" — a host-only cookie. Passing an
        # empty string through would emit `Domain=`, which some clients treat
        # as malformed, so it is normalised to None here.
        "jwt_cookie_domain": os.getenv("JWT_COOKIE_DOMAIN") or None,

        # email — any SMTP server. Without SMTP_HOST and MAIL_FROM the
        # mailer logs messages instead of sending them, which is what local
        # development and a desktop install want.
        "smtp_host": os.getenv("SMTP_HOST", ""),
        "smtp_port": _env_port("SMTP_PORT", 0),
        "smtp_username": os.getenv("SMTP_USERNAME", ""),
        "smtp_password": os.getenv("SMTP_PASSWORD", ""),
        "smtp_security": os.getenv("SMTP_SECURITY", "starttls"),
        "mail_from": os.getenv("MAIL_FROM", ""),

        # web push — the deployment's VAPID pair (bootstrap/generate_vapid_keys.py).
        # Without it the composer's notifications fall back to email alone.
        "vapid_public_key": os.getenv("VAPID_PUBLIC_KEY", ""),
        "vapid_private_key": os.getenv("VAPID_PRIVATE_KEY", ""),
        "vapid_subject": os.getenv("VAPID_SUBJECT", ""),

        # Where the browser reaches this app — the base for links we email
        # out. In development the SPA is served by the dev server on another
        # port, so this is NOT the API's own base_url.
        "public_app_url": os.getenv("PUBLIC_APP_URL", "http://localhost:4200"),
        # Where providers send the browser back after consent — the one
        # redirect URI registered with every connected app. Empty means
        # PUBLIC_APP_URL + /oauth/callback, which a proxied deployment
        # serves; development, with the API on its own port, sets it.
        "oauth_redirect_url": os.getenv("OAUTH_REDIRECT_URL", ""),

        # backend -> runtime service identity (RS256; runtime holds the
        # public key)
        "backend_service_private_key": _env_pem("BACKEND_SERVICE_PRIVATE_KEY"),

        # where the AI runtime is reachable
        "ai_runtime_url": os.getenv("AI_RUNTIME_URL", "http://127.0.0.1:8001"),

        # Where approved agent code is kept.
        "agent_package_dir": os.getenv(
            "AGENT_PACKAGE_DIR", "data/agent-packages"),

        # No trust ceiling, and no turn/step budgets. Trust is the
        # person's choice, bounded by what they may do at all: entitlement
        # decides which functions exist for them, the autonomous-schedules
        # grant decides who may let standing work act unattended, and the
        # invocation gate decides what stops to ask. Turns are the user's
        # patience and steps are the agent manifest's to declare, so
        # neither is a property of this process.

        # A repository the Marketplace offers as a source with one click.
        # Configuration, never a default: unset, it offers none.
        "reference_catalog_url": os.getenv("REFERENCE_CATALOG_URL", "").strip(),
        # A folder on this machine whose git repositories may be agent
        # sources: how somebody writing an agent tries it before they
        # publish it. Unset — every server — and a source is fetched
        # from a repository's address, never from the server's own disk.
        "agent_source_folder": os.getenv("AGENT_SOURCE_FOLDER", "").strip(),

        # database
        "mongo_uri": os.getenv("MONGO_URI", "mongodb://localhost:27017"),
        "mongo_database_name": os.getenv("MONGO_DATABASE_NAME", "decentai"),

        # file storage — where uploaded bytes live. Writes go to the
        # configured provider (database/file_connectors); reads follow
        # each document's own storage_provider stamp.
        "file_storage_provider": (
            os.getenv("FILE_STORAGE_PROVIDER", "local").strip().lower()
        ),
        "upload_dir": os.getenv("UPLOADS_DIR", "./uploads"),
        # The largest single upload this deployment accepts. Bytes are
        # held in memory while they are hashed and stored, so this is
        # what stands between an authenticated caller and the process's
        # memory. A cap that exists is worth more than the right cap.
        "max_upload_mb": _env_int("MAX_UPLOAD_MB", 25, 1, 5000),

    }


@dataclass(frozen=True)
class Settings:
    # where the server binds, and which proxy it believes
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    forwarded_allow_ips: str = "127.0.0.1"

    # app
    cors_allow_origins: Optional[List[str]] = None
    # web, or desktop
    deployment_kind: str = "web"

    # session token signing (HS256, one process signs and verifies)
    token_secret_key: Optional[str] = ""
    jwt_cookie_secure: bool = True
    jwt_cookie_samesite: Literal["lax", "strict", "none"] | None = "lax"
    jwt_cookie_domain: Optional[str] = None

    # backend -> runtime service identity (RS256 private key, PEM)
    backend_service_private_key: Optional[str] = None

    # where the AI runtime is reachable
    ai_runtime_url: str = "http://127.0.0.1:8001"
    agent_package_dir: str = "data/agent-packages"

    # database
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_database_name: str = "decentai"

    # file storage
    file_storage_provider: str = "local"
    upload_dir: str = "./uploads"
    max_upload_mb: int = 25

    # email — SMTP
    smtp_host: Optional[str] = ""
    smtp_port: int = 0
    smtp_username: Optional[str] = ""
    smtp_password: Optional[str] = ""
    smtp_security: Optional[str] = "starttls"
    mail_from: Optional[str] = ""

    # web push — VAPID
    vapid_public_key: Optional[str] = ""
    vapid_private_key: Optional[str] = ""
    vapid_subject: Optional[str] = ""

    # public base for emailed links
    public_app_url: Optional[str] = None
    # where connected apps send consent back
    oauth_redirect_url: Optional[str] = ""

    # the Marketplace's one-click source, and where local sources may be
    reference_catalog_url: str = ""
    agent_source_folder: str = ""

    @property
    def is_desktop(self) -> bool:
        return self.deployment_kind == "desktop"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(**_load_settings())
