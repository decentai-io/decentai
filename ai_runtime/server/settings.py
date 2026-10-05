import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_pem(name: str) -> str:
    # PEM keys are stored as single-line env values with \n escapes
    # (env_file-safe); turn them back into a real multiline PEM here.
    raw = (os.getenv(name) or "").strip()
    return raw.replace("\\n", "\n")


def _default_install_dir() -> str:
    """Where installed agent code lives — the content store, one folder
    per package digest (agents/library.py). Writable, and in a
    deployment a volume; in local development point it OUTSIDE the tree
    with AI_RUNTIME_AGENTS_INSTALL_DIR so runtime state never sits
    beside runtime source."""
    return str(Path(__file__).resolve().parent.parent / "installed_agents")


@dataclass(frozen=True)
class RuntimeSettings:
    backend_service_public_key: str = ""
    backend_token_issuer: str = "decentai-backend"
    backend_service_audience: str = "decentai-ai-runtime"
    #: where the platform answers the services contract over /app
    #: (docs/system/chat-session.md). Empty means standalone: the sim serves.
    backend_url: str = ""
    host: str = "0.0.0.0"
    port: int = 8001
    agents_install_dir: str = field(default_factory=_default_install_dir)
    #: Where agents' processes are started, when that is a container of
    #: their own (``agents:8003``, docs/system/sandbox.md). Empty starts
    #: them here, beside the runtime.
    agents_spawner: str = ""
    #: Where the proxy confined workers are held to listens
    #: (docs/system/sandbox.md). The container's firewall rule is written
    #: for this port before the runtime starts, so it is a setting and
    #: not a port picked at start.
    egress_port: int = 8002
    #: The tests' escape, as the web agents have it: the proxy reaches
    #: this machine's own addresses. Never in a deployment.
    egress_allows_loopback: bool = False
    #: Where packages come from: the hosts the builder of an agent's
    #: declared packages may reach, and the whole of them.
    package_hosts: tuple = ("pypi.org", "files.pythonhosted.org")

    @classmethod
    def from_env(cls) -> "RuntimeSettings":
        public_key = _env_pem("BACKEND_SERVICE_PUBLIC_KEY")
        if not public_key:
            raise ValueError(
                "BACKEND_SERVICE_PUBLIC_KEY is required — the runtime cannot "
                "verify that connections come from the backend without it."
            )
        return cls(
            backend_service_public_key=public_key,
            backend_token_issuer=os.getenv("BACKEND_TOKEN_ISSUER", "decentai-backend"),
            backend_service_audience=os.getenv("AI_RUNTIME_TOKEN_AUDIENCE", "decentai-ai-runtime"),
            backend_url=os.getenv("BACKEND_INTERNAL_URL", "").strip(),
            host=os.getenv("AI_RUNTIME_HOST", "0.0.0.0"),
            port=int(os.getenv("AI_RUNTIME_PORT", "8001")),
            agents_install_dir=(
                os.getenv("AI_RUNTIME_AGENTS_INSTALL_DIR", "")
                or _default_install_dir()
            ),
            agents_spawner=os.getenv("AI_RUNTIME_AGENTS_SPAWNER", "").strip(),
            egress_port=int(os.getenv("AI_RUNTIME_EGRESS_PORT", "8002")),
            egress_allows_loopback=(
                os.getenv("DECENTAI_WEB_ALLOW_LOOPBACK", "") == "1"),
            package_hosts=cls._hosts(
                os.getenv("AI_RUNTIME_PACKAGE_HOSTS", ""))
            or cls.package_hosts,
        )

    @staticmethod
    def _hosts(said: str) -> tuple:
        """Host names, as a setting lists them: separated by
        commas or spaces."""
        return tuple(
            host.strip().lower()
            for host in str(said or "").replace(",", " ").split()
            if host.strip())
