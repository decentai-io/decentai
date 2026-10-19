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
    #: The Data Core's public key: nothing it did not sign is believed
    #: (door/node.py, docs/system/execution-nodes.md).
    data_core_service_public_key: str = ""
    #: Where this execution node's credential is: a file its operator's
    #: secret manager mounts and keeps fresh, or an issuer it asks. One
    #: of the two is required — a node with no credential is admitted
    #: nowhere. The node never holds an authority's private key.
    node_credential_file: str = ""
    node_credential_url: str = ""
    #: where the Data Core answers the services contract over /app
    #: (docs/system/chat-session.md), and where the node link is
    #: dialed (docs/system/execution-nodes.md).
    data_core_url: str = ""
    host: str = "0.0.0.0"
    port: int = 8001
    agents_install_dir: str = field(default_factory=_default_install_dir)
    #: An Execution Sandbox this runtime knows by its address
    #: (``sandbox:8003``), besides those that register with it. Optional:
    #: sandboxes register themselves (docs/system/sandbox.md).
    sandbox_address: str = ""
    #: The secret set for the execution node: what a sandbox proves to
    #: register, and what the runtime proves on every request to one.
    sandbox_secret: str = ""
    #: Where Execution Sandboxes register with this runtime.
    sandbox_port: int = 8005
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
        public_key = _env_pem("DATA_CORE_SERVICE_PUBLIC_KEY")
        if not public_key:
            raise ValueError(
                "DATA_CORE_SERVICE_PUBLIC_KEY is required — this execution node "
                "believes nothing the Data Core did not sign, and cannot tell "
                "without it.")
        credential_file = os.getenv("AI_RUNTIME_NODE_CREDENTIAL_FILE", "").strip()
        credential_url = os.getenv("AI_RUNTIME_NODE_CREDENTIAL_URL", "").strip()
        if not (credential_file or credential_url):
            raise ValueError(
                "AI_RUNTIME_NODE_CREDENTIAL_FILE (or AI_RUNTIME_NODE_"
                "CREDENTIAL_URL) is required — an execution node is admitted "
                "by a credential an authority signed, read from where its "
                "operator puts it (docs/system/execution-nodes.md).")
        data_core_url = os.getenv("DATA_CORE_INTERNAL_URL", "").strip()
        if not data_core_url:
            raise ValueError(
                "DATA_CORE_INTERNAL_URL is required — the runtime keeps nothing "
                "itself, and asks the Data Core for everything a chat is.")
        sandbox_address = os.getenv("AI_RUNTIME_SANDBOX_ADDRESS", "").strip()
        sandbox_secret = os.getenv("AI_RUNTIME_SANDBOX_SECRET", "").strip()
        if not sandbox_secret:
            raise ValueError(
                "AI_RUNTIME_SANDBOX_SECRET is required — the runtime starts "
                "no process of an agent's itself; its Execution Sandboxes "
                "do, and register with it proving this secret, set for the "
                "execution node (bootstrap/setup.py writes one).")
        return cls(
            data_core_service_public_key=public_key,
            node_credential_file=credential_file,
            node_credential_url=credential_url,
            data_core_url=data_core_url,
            host=os.getenv("AI_RUNTIME_HOST", "0.0.0.0"),
            port=cls._port("AI_RUNTIME_PORT", 8001),
            agents_install_dir=(
                os.getenv("AI_RUNTIME_AGENTS_INSTALL_DIR", "")
                or _default_install_dir()
            ),
            sandbox_address=sandbox_address,
            sandbox_secret=sandbox_secret,
            sandbox_port=cls._port("AI_RUNTIME_SANDBOX_PORT", 8005),
            # As start.sh reads it, which wrote the firewall rule with
            # it: a value that is not a number is the default.
            egress_port=cls._port("AI_RUNTIME_EGRESS_PORT", 8002,
                                  or_default=True),
            egress_allows_loopback=(
                os.getenv("DECENTAI_WEB_ALLOW_LOOPBACK", "") == "1"),
            package_hosts=cls._hosts(
                os.getenv("AI_RUNTIME_PACKAGE_HOSTS", ""))
            or cls.package_hosts,
        )

    @staticmethod
    def _port(name: str, default: int, or_default: bool = False) -> int:
        """A port a setting names: the default where it is left blank,
        and a refusal that names the setting where it is not a port."""
        said = os.getenv(name, "").strip()
        if not said:
            return default
        if said.isdigit() and 1 <= int(said) <= 65535:
            return int(said)
        if or_default:
            return default
        raise ValueError(
            f"{name} must be a port, a number from 1 to 65535; "
            f"it is '{said}'.")

    @staticmethod
    def _hosts(said: str) -> tuple:
        """Host names, as a setting lists them: separated by
        commas or spaces."""
        return tuple(
            host.strip().lower()
            for host in str(said or "").replace(",", " ").split()
            if host.strip())
