"""Manifest v1 validation and typed access (docs/agents/manifest.md).

Shared by both sides of the boundary: the backend validates manifests at
installation approval, the runtime validates them at load — with this one
implementation, so they can never disagree. The validator returns a list
of error strings — empty means valid — mirroring how the platform rejects
pathological input at the boundary instead of evaluating it.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

SCHEMA_VERSION = "1.0"

ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
AGENT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
ENTRYPOINT_PATTERN = re.compile(r"^[A-Za-z0-9_.]+:[A-Za-z_][A-Za-z0-9_]*$")

RESOURCE_KINDS = ("secrets", "data", "files")
FIELD_TYPES = {"string", "number", "select", "object", "secret"}
STORAGE_KINDS = {"keys", "values"}
#: What the platform writes into a connected account's credential; a
#: manifest may not declare these names itself.
OAUTH_FIELD_NAMES = ("account", "access_token", "refresh_token", "expires_at", "status")
PROVIDER_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
#: Everything an oauth block may say. The last five are how a provider
#: departs from the common shape (docs/agents/manifest.md).
OAUTH_KEYS = {"provider", "authorize_url", "token_url", "scopes",
              "authorize_params", "identity", "scope_param",
              "scope_separator", "token_auth", "token_format", "token_path"}
#: The closed choices among them; the first value is the default.
OAUTH_CHOICES = {
    "scope_separator": (" ", ","),   # Slack and Todoist join with commas
    "token_auth": ("body", "basic"),  # Notion wants the client in Basic auth
    "token_format": ("form", "json"),  # Notion takes a JSON body
}
OAUTH_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
OAUTH_PATH_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,5}$")
OPERATIONS = {"list", "read", "use", "create", "update", "delete"}
NORMALIZATIONS = {"uppercase", "lowercase"}
CARDINALITIES = {"one", "many"}
PERMISSION_LEVELS = {0, 1, 2, 3}

# What a person may be allowed to do to a resource directly, per kind.
# read/list and delete are deliberately absent from this vocabulary: the
# owner always sees their own data and can always remove it — neither is
# the manifest's to grant, and no declaration may make data undeletable.
# Files have no update because a file's content is immutable; replacing
# one is a delete and a create.
USER_ACCESS_OPERATIONS = {"data": {"create", "update"}, "files": {"create"}}

# The hosts an agent connects to. A name, with `*.` in front for every
# host under it; never an address, which would name a machine and not
# a service.
NETWORK_ANY = "any"
MAX_NETWORK_HOSTS = 50
HOST_PATTERN = re.compile(
    r"^(\*\.)?([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$")
ADDRESS_PATTERN = re.compile(r"^[0-9.]+$|^\[")

# x-resource "type" value -> the manifest resources block it references.
X_RESOURCE_KINDS = {"secret": "secrets", "data": "data", "file": "files"}

def manifest_hash(document: Any) -> str:
    """A stable fingerprint of one manifest document.

    Both sides compute this and compare the answers: the backend records
    it when an administrator approves a manifest, and the runtime checks
    it against the manifest inside the package before it replaces a
    working agent. So it is here, with the validator, for the reason
    stated at the top of this file — it was written out twice, once in
    each process, and two implementations of a fingerprint agree only
    until one of them is edited. A drift of one separator would fail
    every install with "the manifest is not the one that was approved",
    which is true of nothing and points at nothing.

    sort_keys because YAML preserves author order and JSON must not
    inherit it; the tight separators because whitespace is not content.
    """
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":"))
        .encode("utf-8")
    ).hexdigest()


MAX_TOOLS = 16
MAX_FUNCTIONS_PER_TOOL = 25
MAX_SCHEMA_DEPTH = 5

SCHEMA_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}
SCHEMA_KEYWORDS = {
    "type", "properties", "required", "additionalProperties", "items",
    "enum", "const", "default", "minimum", "maximum", "minLength",
    "maxLength", "pattern", "format", "minItems", "maxItems",
    "minProperties",
}
FORBIDDEN_KEYWORDS = {
    "$ref", "$defs", "if", "then", "else", "allOf", "anyOf", "oneOf", "not",
}


class ManifestValidator:
    """One-shot validator: ``validate(document)`` -> error list; [] = valid."""

    def validate(self, document: Any) -> List[str]:
        self.errors: List[str] = []
        self.scopes: Dict[str, dict] = {}
        # kind -> {resource_id: resource dict}, plus a flat id set for the
        # per-agent uniqueness rule (categories share one derived namespace).
        self.resources: Dict[str, Dict[str, dict]] = {k: {} for k in RESOURCE_KINDS}

        if not isinstance(document, dict):
            return ["manifest must be a mapping"]

        if document.get("schema_version") != SCHEMA_VERSION:
            self.errors.append(
                f"schema_version must be \"{SCHEMA_VERSION}\""
            )

        self._agent(document.get("agent"))
        self._implementation(document.get("implementation"))
        self._authorization(document.get("authorization"))
        self._resources(document.get("resources"))
        self._network(document.get("network"))
        self._tools(document.get("tools"))

        return self.errors

    # ------------------------------------------------------------------
    def _fail(self, path: str, message: str) -> None:
        self.errors.append(f"{path}: {message}")

    @staticmethod
    def _is_str(value: Any) -> bool:
        return isinstance(value, str) and value.strip() != ""

    def _require_str(self, container: dict, key: str, path: str) -> None:
        if not self._is_str(container.get(key)):
            self._fail(path, f"{key} is required")

    # ------------------------------------------------------------------
    def _agent(self, agent: Any) -> None:
        if not isinstance(agent, dict):
            self.errors.append("agent block is required")
            return

        agent_id = agent.get("id")
        if not isinstance(agent_id, str) or not AGENT_ID_PATTERN.match(agent_id):
            self._fail("agent.id", "must match ^[a-z][a-z0-9_]{1,31}$")

        self._require_str(agent, "name", "agent")
        self._require_str(agent, "description", "agent")

        version = agent.get("version")
        if not isinstance(version, str) or not VERSION_PATTERN.match(version):
            self._fail("agent.version", "must be a semver string like \"1.0.0\"")

        tags = agent.get("tags", [])
        if not isinstance(tags, list) or not all(self._is_str(t) for t in tags):
            self._fail("agent.tags", "must be a list of strings")

        instructions = agent.get("instructions", "")
        if instructions is not None and not isinstance(instructions, str):
            self._fail("agent.instructions", "must be a string")

        # What to try first: a few prompts a person can send as they
        # are, each with a title. Shown on the agent's page and offered
        # as a chat's opening, so an agent explains itself by example.
        examples = agent.get("examples")
        if examples is not None:
            if not isinstance(examples, list) or len(examples) > 6:
                self._fail("agent.examples", "must be a list of at most 6 {title, prompt}")
            else:
                for index, example in enumerate(examples):
                    path = f"agent.examples[{index}]"
                    if not isinstance(example, dict):
                        self._fail(path, "must be a mapping with title and prompt")
                        continue
                    if not self._is_str(example.get("title")) or len(example["title"]) > 80:
                        self._fail(f"{path}.title", "must be a string of at most 80 characters")
                    if not self._is_str(example.get("prompt")) or len(example["prompt"]) > 500:
                        self._fail(f"{path}.prompt", "must be a string of at most 500 characters")


    def _implementation(self, implementation: Any) -> None:
        if not isinstance(implementation, dict):
            self.errors.append("implementation block is required")
            return

        entrypoint = implementation.get("entrypoint")
        if not isinstance(entrypoint, str) or not ENTRYPOINT_PATTERN.match(entrypoint):
            self._fail(
                "implementation.entrypoint",
                "must match <module>:<ClassName>",
            )

        dependencies = implementation.get("dependencies", [])
        if not isinstance(dependencies, list) or not all(
            self._is_str(d) for d in dependencies
        ):
            self._fail(
                "implementation.dependencies",
                "must be a list of pip requirement strings",
            )

    def _authorization(self, authorization: Any) -> None:
        if authorization is None:
            return
        if not isinstance(authorization, dict):
            self.errors.append("authorization must be a mapping")
            return

        scopes = authorization.get("scopes", {})
        if scopes is None:
            scopes = {}
        if not isinstance(scopes, dict):
            self._fail("authorization.scopes", "must be a mapping")
            return

        for name, scope in scopes.items():
            path = f"authorization.scopes.{name}"
            if not isinstance(name, str) or not ID_PATTERN.match(name):
                self._fail(path, "scope name must match ^[a-z][a-z0-9_]*$")
                continue
            if not isinstance(scope, dict):
                self._fail(path, "must be a mapping")
                continue
            if scope.get("type") != "string":
                self._fail(path, "type must be \"string\" in schema v1")
            self._require_str(scope, "description", path)
            normalization = scope.get("normalization")
            if normalization is not None and normalization not in NORMALIZATIONS:
                self._fail(
                    path, "normalization must be uppercase or lowercase"
                )
            self.scopes[name] = scope

    # ------------------------------------------------------------------
    def _resources(self, resources: Any) -> None:
        if resources is None:
            return
        if not isinstance(resources, dict):
            self.errors.append("resources must be a mapping")
            return

        unknown = set(resources) - set(RESOURCE_KINDS)
        if unknown:
            self._fail("resources", f"unknown resource kinds: {sorted(unknown)}")

        seen_ids: Dict[str, str] = {}  # resource id -> kind (shared namespace)

        for kind in RESOURCE_KINDS:
            declared = resources.get(kind, [])
            if declared is None:
                declared = []
            if not isinstance(declared, list):
                self._fail(f"resources.{kind}", "must be a list")
                continue

            for index, resource in enumerate(declared):
                path = f"resources.{kind}[{index}]"
                if not isinstance(resource, dict):
                    self._fail(path, "must be a mapping")
                    continue

                resource_id = resource.get("id")
                if not isinstance(resource_id, str) or not ID_PATTERN.match(resource_id):
                    self._fail(path, "id must match ^[a-z][a-z0-9_]*$")
                    continue
                if resource_id in seen_ids:
                    self._fail(
                        path,
                        f"duplicate resource id '{resource_id}' (also declared "
                        f"under {seen_ids[resource_id]}; ids share one "
                        f"derived namespace)",
                    )
                    continue
                seen_ids[resource_id] = kind

                self._require_str(resource, "label", path)
                self._binding(resource.get("binding"), path)

                # `family` used to opt a secret into a SHARED definition —
                # the agent named a slug and joined whatever was already
                # there, which meant claiming a name was enough to reach
                # somebody else's stored credential. Refused rather than
                # ignored: resource keys are not whitelisted, so silently
                # dropping it would leave an agent that relied on joining
                # quietly deriving its own empty definition and failing
                # later, far from the cause.
                if resource.get("family") is not None:
                    self._fail(
                        path,
                        "family is no longer supported: a credential is "
                        "granted to this agent by name, not claimed by "
                        "declaring a shared slug",
                    )

                self._user_access(resource.get("user_access"), kind, path)

                if kind == "secrets" and resource.get("oauth") is not None:
                    # A connected account: the platform runs the consent
                    # flow and adds the token fields itself, so the
                    # manifest may declare no fields at all.
                    self._oauth(resource.get("oauth"), path)
                    self._fields(resource.get("fields"), path, optional=True,
                                 reserved=OAUTH_FIELD_NAMES)
                elif kind in ("secrets", "data"):
                    self._fields(resource.get("fields"), path)
                else:
                    self._file_constraints(resource.get("constraints"), path)

                self.resources[kind][resource_id] = resource

    # ------------------------------------------------------------------
    def _network(self, network: Any) -> None:
        """The hosts an agent connects to (docs/system/sandbox.md).

        Required: an agent says where it connects, and an administrator
        approves that. It is the whole of what the agent may reach: a
        list of names, each a host, a host under a wildcard, or the
        host a granted credential names — empty for an agent that
        connects to nothing — or the one word ``any``."""
        if network is None:
            self.errors.append(
                "network block is required: say where the agent connects "
                "(hosts: [] for nowhere outside the platform)")
            return
        if not isinstance(network, dict):
            self.errors.append("network must be a mapping")
            return
        unknown = set(network) - {"hosts"}
        if unknown:
            self._fail("network", f"unknown keys: {sorted(unknown)}")

        hosts = network.get("hosts")
        if hosts == NETWORK_ANY:
            return
        if not isinstance(hosts, list):
            self._fail("network.hosts",
                       f"must be a list of hosts, or the word {NETWORK_ANY}")
            return
        if len(hosts) > MAX_NETWORK_HOSTS:
            self._fail("network.hosts",
                       f"too many hosts (at most {MAX_NETWORK_HOSTS})")

        seen = set()
        for index, host in enumerate(hosts):
            path = f"network.hosts[{index}]"
            if isinstance(host, dict):
                self._host_from_secret(host, path)
                key = ("from_secret", str(host.get("from_secret")),
                       str(host.get("port")))
            elif isinstance(host, str):
                self._host_name(host, path)
                key = ("name", host)
            else:
                self._fail(path, "must be a host name, or {from_secret: "
                                 "<secret>.<field>}")
                continue
            if key in seen:
                self._fail(path, "is declared twice")
            seen.add(key)

    def _host_name(self, host: str, path: str) -> None:
        """A name, or a name and the port it is reached on
        (``imap.example.com:993``). Without a port a host is reached
        where the web answers, on 443 and 80; with one, on that port
        and no other — for an agent that speaks a protocol that is not
        the web's."""
        name, colon, port = host.rpartition(":")
        if not colon or host.startswith("[") or "/" in host:
            name, port = host, ""
        elif not self._is_port(port):
            self._fail(path, "a port is a number from 1 to 65535, written "
                             "after the name: imap.example.com:993")
            return
        if name in ("*", NETWORK_ANY):
            self._fail(path, f"every host is `hosts: {NETWORK_ANY}`, said "
                             "once and in place of the list")
        elif ADDRESS_PATTERN.match(name) or ":" in name or "/" in name:
            self._fail(path, "must be a name — not an address, and without "
                             "a scheme or a path")
        elif not HOST_PATTERN.match(name):
            self._fail(path, "must be a lowercase host name such as "
                             "api.example.com, or *.example.com for every "
                             "host under it")

    @staticmethod
    def _is_port(port: Any) -> bool:
        if isinstance(port, bool):
            return False
        if isinstance(port, str):
            if not port.isdigit() or port != str(int(port)):
                return False
            port = int(port)
        return isinstance(port, int) and 1 <= port <= 65535

    def _host_from_secret(self, host: dict, path: str) -> None:
        """A host that is the person's to say — their own site, their
        own server — named by a field of the credential they grant."""
        if not {"from_secret"} <= set(host) <= {"from_secret", "port"} \
                or not isinstance(host.get("from_secret"), str):
            self._fail(path, "must be {from_secret: <secret>.<field>}, "
                             "with a port when it is not the web's")
            return
        if "port" in host and (isinstance(host["port"], str)
                               or not self._is_port(host["port"])):
            self._fail(path, "a port is a number from 1 to 65535")
            return
        secret_id, _, field_name = host["from_secret"].partition(".")
        secret = self.resources["secrets"].get(secret_id)
        if secret is None:
            self._fail(path, f"'{secret_id}' is not a secret this agent declares")
            return
        field = next((f for f in secret.get("fields") or []
                      if isinstance(f, dict) and f.get("name") == field_name), None)
        if field is None:
            self._fail(path, f"'{field_name}' is not a field of '{secret_id}'")
        elif field.get("type") != "string" or field.get("storage") != "keys":
            self._fail(path, "the field must be a string stored in keys — "
                             "an address is shown to whoever approves, and "
                             "a secret is not")

    def _user_access(self, user_access: Any, kind: str, path: str) -> None:
        """What a person may do to this resource directly, from the
        records UI, with no agent in the loop. Absent means none."""
        if user_access is None:
            return
        if kind == "secrets":
            self._fail(
                f"{path}.user_access",
                "secrets have no user_access — a credential is edited on "
                "the secrets surface, never through a records UI",
            )
            return
        if not isinstance(user_access, list) or not user_access:
            self._fail(
                f"{path}.user_access",
                "must be a non-empty list of operations",
            )
            return

        allowed = USER_ACCESS_OPERATIONS[kind]
        for operation in user_access:
            if operation in ("read", "list"):
                self._fail(
                    f"{path}.user_access",
                    f"'{operation}' is not declarable — the owner always "
                    "sees their own data",
                )
            elif operation == "delete":
                self._fail(
                    f"{path}.user_access",
                    "'delete' is not declarable — a person can always "
                    "remove their own records, and no manifest may say "
                    "otherwise",
                )
            elif operation == "update" and kind == "files":
                self._fail(
                    f"{path}.user_access",
                    "'update' — a file's content is immutable; replacing "
                    "one is a delete and a create",
                )
            elif operation not in allowed:
                self._fail(
                    f"{path}.user_access",
                    f"unknown operation '{operation}' "
                    f"(allowed for {kind}: {sorted(allowed)})",
                )
        if len(set(user_access)) != len(user_access):
            self._fail(f"{path}.user_access", "duplicate operations")

    def _binding(self, binding: Any, path: str) -> None:
        if not isinstance(binding, dict):
            self._fail(path, "binding is required")
            return
        if binding.get("cardinality") not in CARDINALITIES:
            self._fail(f"{path}.binding", "cardinality must be one or many")
        required = binding.get("required", False)
        if not isinstance(required, bool):
            self._fail(f"{path}.binding", "required must be a boolean")

    def _oauth(self, oauth: Any, path: str) -> None:
        """A credential obtained by consent rather than typed in.

        The manifest carries what is the PROVIDER's to know — where
        consent starts, where tokens are minted, which scopes — and
        names the provider with a short id. The organization's own
        registration with that provider (client id and secret) is not
        the manifest's business: it is looked up by the id at connect
        time, so two agents saying ``google`` share one registration."""
        path = f"{path}.oauth"
        if not isinstance(oauth, dict):
            self._fail(path, "must be a mapping")
            return
        provider = oauth.get("provider")
        if not isinstance(provider, str) or not PROVIDER_PATTERN.match(provider):
            self._fail(f"{path}.provider",
                       "must be a short lowercase id such as google or microsoft")
        for key in ("authorize_url", "token_url"):
            value = oauth.get(key)
            if not isinstance(value, str) or not value.startswith("https://"):
                self._fail(f"{path}.{key}", "must be an https:// URL")
        scopes = oauth.get("scopes")
        if not isinstance(scopes, list) or not scopes or not all(
                self._is_str(s) for s in scopes):
            self._fail(f"{path}.scopes", "must be a non-empty list of strings")
        params = oauth.get("authorize_params")
        if params is not None and not (
                isinstance(params, dict) and all(
                    isinstance(k, str) and isinstance(v, str) for k, v in params.items())):
            self._fail(f"{path}.authorize_params", "must be a mapping of strings")
        # How this provider departs from the common shape. Every key is
        # optional and its default is what Google and Microsoft do, so a
        # block that names none of them behaves exactly as before.
        for key, allowed in OAUTH_CHOICES.items():
            if key in oauth and oauth[key] not in allowed:
                self._fail(f"{path}.{key}", f"must be one of {list(allowed)}")
        if "scope_param" in oauth and not (
                isinstance(oauth["scope_param"], str)
                and OAUTH_NAME_PATTERN.match(oauth["scope_param"])):
            self._fail(f"{path}.scope_param",
                       "must be a query parameter name such as scope or user_scope")
        if "token_path" in oauth and not (
                isinstance(oauth["token_path"], str)
                and OAUTH_PATH_PATTERN.match(oauth["token_path"])):
            self._fail(f"{path}.token_path",
                       "must be a dotted path such as authed_user")
        identity = oauth.get("identity")
        if identity is not None:
            if not isinstance(identity, dict):
                self._fail(f"{path}.identity", "must be a mapping with url and field")
            else:
                self._identity(identity, f"{path}.identity")
        unknown = set(oauth) - OAUTH_KEYS
        if unknown:
            self._fail(path, f"unknown keys: {sorted(unknown)}")

    def _identity(self, identity: dict, path: str) -> None:
        """Whose account a connection is. Read from a URL called with the
        new token (GET unless the provider wants POST, with any headers
        it insists on), or — when the provider says so in the token
        response itself — from that response. ``field`` is a dotted
        path either way."""
        source = identity.get("source", "url")
        if source not in ("url", "token"):
            self._fail(f"{path}.source", "must be url or token")
        url = identity.get("url")
        if source == "url" and not (isinstance(url, str) and url.startswith("https://")):
            self._fail(f"{path}.url", "must be an https:// URL")
        if source == "token" and url is not None:
            self._fail(f"{path}.url", "is not used when source is token")
        field = identity.get("field", "email")
        if not (isinstance(field, str) and OAUTH_PATH_PATTERN.match(field)):
            self._fail(f"{path}.field", "must be a dotted path such as email")
        if identity.get("method", "GET") not in ("GET", "POST"):
            self._fail(f"{path}.method", "must be GET or POST")
        headers = identity.get("headers")
        if headers is not None and not (
                isinstance(headers, dict) and all(
                    isinstance(k, str) and isinstance(v, str)
                    for k, v in headers.items())):
            self._fail(f"{path}.headers", "must be a mapping of strings")
        unknown = set(identity) - {"url", "field", "method", "headers", "source"}
        if unknown:
            self._fail(path, f"unknown keys: {sorted(unknown)}")

    def _fields(self, fields: Any, path: str, optional: bool = False,
                reserved: tuple = ()) -> None:
        if optional and not fields:
            return
        if not isinstance(fields, list) or not fields:
            self._fail(path, "fields must be a non-empty list")
            return

        seen = set()
        for index, field in enumerate(fields):
            field_path = f"{path}.fields[{index}]"
            if not isinstance(field, dict):
                self._fail(field_path, "must be a mapping")
                continue

            name = field.get("name")
            if not isinstance(name, str) or not ID_PATTERN.match(name):
                self._fail(field_path, "name must match ^[a-z][a-z0-9_]*$")
                continue
            if name in seen:
                self._fail(field_path, f"duplicate field name '{name}'")
            seen.add(name)
            if name in reserved:
                self._fail(field_path,
                           f"'{name}' is filled in by the platform's OAuth flow")

            if field.get("type") not in FIELD_TYPES:
                self._fail(
                    field_path, f"type must be one of {sorted(FIELD_TYPES)}"
                )
            if field.get("storage") not in STORAGE_KINDS:
                self._fail(field_path, "storage must be keys or values")

            # Keys are the queryable half, and the data layer holds them
            # to scalars — text, numbers, booleans. A manifest declaring
            # an object in keys installs an agent whose writes can only
            # ever fail; a secret in keys would be a credential stored in
            # plaintext. Both are contradictions to refuse here, at
            # review time, rather than discoveries for the first user
            # who tries to save something.
            if field.get("storage") == "keys":
                if field.get("type") == "object":
                    self._fail(
                        field_path,
                        "an object cannot be stored in keys — keys are "
                        "scalar and queryable; use storage: values, or a "
                        "scalar type",
                    )
                if field.get("type") == "secret":
                    self._fail(
                        field_path,
                        "a secret cannot be stored in keys — keys are "
                        "plaintext; secret fields need storage: values",
                    )
            required = field.get("required", False)
            if not isinstance(required, bool):
                self._fail(field_path, "required must be a boolean")

            if field.get("type") == "select":
                options = field.get("options")
                if not isinstance(options, list) or not options or not all(
                    self._is_str(o) for o in options
                ):
                    self._fail(
                        field_path,
                        "select fields must declare options "
                        "(non-empty list of strings)",
                    )

    def _file_constraints(self, constraints: Any, path: str) -> None:
        if constraints is None:
            return
        if not isinstance(constraints, dict):
            self._fail(f"{path}.constraints", "must be a mapping")
            return

        mime_types = constraints.get("mime_types")
        if mime_types is not None and (
            not isinstance(mime_types, list)
            or not mime_types
            or not all(self._is_str(m) for m in mime_types)
        ):
            self._fail(
                f"{path}.constraints",
                "mime_types must be a non-empty list of strings",
            )

        max_size = constraints.get("max_size_mb")
        if max_size is not None and (
            isinstance(max_size, bool)
            or not isinstance(max_size, (int, float))
            or max_size <= 0
        ):
            self._fail(f"{path}.constraints", "max_size_mb must be positive")

    # ------------------------------------------------------------------
    def _tools(self, tools: Any) -> None:
        if not isinstance(tools, list) or not tools:
            self.errors.append("tools must be a non-empty list")
            return
        if len(tools) > MAX_TOOLS:
            self.errors.append(f"too many tools (max {MAX_TOOLS})")

        seen = set()
        for index, tool in enumerate(tools):
            path = f"tools[{index}]"
            if not isinstance(tool, dict):
                self._fail(path, "must be a mapping")
                continue

            tool_id = tool.get("id")
            if not isinstance(tool_id, str) or not ID_PATTERN.match(tool_id):
                self._fail(path, "id must match ^[a-z][a-z0-9_]*$")
                continue
            if tool_id in seen:
                self._fail(path, f"duplicate tool id '{tool_id}'")
            seen.add(tool_id)

            self._require_str(tool, "name", path)
            self._require_str(tool, "description", path)

            tool_resources = self._tool_resources(tool.get("resources"), path)

            functions = tool.get("functions")
            if not isinstance(functions, list) or not functions:
                self._fail(path, "functions must be a non-empty list")
                continue
            if len(functions) > MAX_FUNCTIONS_PER_TOOL:
                self._fail(
                    path, f"too many functions (max {MAX_FUNCTIONS_PER_TOOL})"
                )

            seen_functions = set()
            for fn_index, function in enumerate(functions):
                fn_path = f"{path}.functions[{fn_index}]"
                fn_id = self._function(function, fn_path, tool_resources)
                if fn_id:
                    if fn_id in seen_functions:
                        self._fail(fn_path, f"duplicate function id '{fn_id}'")
                    seen_functions.add(fn_id)

    def _tool_resources(self, declared: Any, path: str) -> Dict[str, set]:
        """Validate a tool's resource references; return kind -> allowed ids."""
        allowed: Dict[str, set] = {kind: set() for kind in RESOURCE_KINDS}
        if declared is None:
            return allowed
        if not isinstance(declared, dict):
            self._fail(f"{path}.resources", "must be a mapping")
            return allowed

        for kind, ids in declared.items():
            if kind not in RESOURCE_KINDS:
                self._fail(f"{path}.resources", f"unknown resource kind '{kind}'")
                continue
            if not isinstance(ids, list):
                self._fail(f"{path}.resources.{kind}", "must be a list of ids")
                continue
            for resource_id in ids:
                if resource_id not in self.resources[kind]:
                    self._fail(
                        f"{path}.resources.{kind}",
                        f"'{resource_id}' is not a declared agent resource",
                    )
                    continue
                allowed[kind].add(resource_id)
        return allowed

    def _function(
        self, function: Any, path: str, tool_resources: Dict[str, set]
    ) -> Optional[str]:
        if not isinstance(function, dict):
            self._fail(path, "must be a mapping")
            return None

        fn_id = function.get("id")
        if not isinstance(fn_id, str) or not ID_PATTERN.match(fn_id):
            self._fail(path, "id must match ^[a-z][a-z0-9_]*$")
            fn_id = None

        self._require_str(function, "name", path)
        self._require_str(function, "description", path)

        level = function.get("permission_level")
        if isinstance(level, bool) or level not in PERMISSION_LEVELS:
            self._fail(
                path, "permission_level is required and must be 0, 1, 2, or 3"
            )

        timeout = function.get("timeout_seconds")
        if timeout is not None and (
            isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1
        ):
            self._fail(path, "timeout_seconds must be a positive integer")

        # `llm: true` asks the platform to hand this function the chat's
        # model as a mediated call — part of the reviewed contract, since
        # an approver should know which functions think with the LLM.
        uses_llm = function.get("llm")
        if uses_llm is not None and not isinstance(uses_llm, bool):
            self._fail(path, "llm must be true or false")

        # `credentials: true` says this function may ask the person for
        # a login as it works (call.credential) — part of the reviewed
        # contract, since an approver should know which functions may
        # be handed a password.
        asks_logins = function.get("credentials")
        if asks_logins is not None and not isinstance(asks_logins, bool):
            self._fail(path, "credentials must be true or false")

        # `code: true` says this function runs code it puts before the
        # person first (call.propose): the hosts an allowed card named
        # are opened for that call, and the packages it named may be
        # installed — part of the reviewed contract, since an approver
        # should know which functions reach what was not declared here.
        runs_code = function.get("code")
        if runs_code is not None and not isinstance(runs_code, bool):
            self._fail(path, "code must be true or false")

        # `watch: true` names the function the platform calls, without
        # the model, when the person asks to see this agent's screen —
        # a browser opened before anything is asked of it. One per
        # agent is enough; the first declared is the one used.
        watches = function.get("watch")
        if watches is not None and not isinstance(watches, bool):
            self._fail(path, "watch must be true or false")
        if watches is True:
            # The platform calls it when a person opens the screen, not
            # because they approved anything and with no model behind
            # it: a card asking to allow it would be asking about a
            # click, and there is no chat's model to answer.
            if function.get("permission_level") != 0:
                self._fail(
                    f"{path}.watch",
                    "a watch function must be permission_level 0 — the "
                    "platform calls it when the person opens the screen, "
                    "and nothing was asked that could be approved")
            if function.get("llm") is True:
                self._fail(
                    f"{path}.watch",
                    "a watch function cannot declare llm: true — the "
                    "platform calls it without the model")

        self._function_resources(function.get("resources"), path, tool_resources)

        inputs = function.get("inputs")
        input_properties = self._schema(inputs, f"{path}.inputs")
        self._schema(function.get("outputs"), f"{path}.outputs")

        self._schedulable(function.get("schedulable"), path, function)

        self._function_authorization(
            function.get("authorization"), path, input_properties
        )
        return fn_id

    def _function_resources(
        self, declared: Any, path: str, tool_resources: Dict[str, set]
    ) -> None:
        if declared is None:
            return
        if not isinstance(declared, dict):
            self._fail(f"{path}.resources", "must be a mapping")
            return

        for kind, entries in declared.items():
            if kind not in RESOURCE_KINDS:
                self._fail(f"{path}.resources", f"unknown resource kind '{kind}'")
                continue
            if not isinstance(entries, dict):
                self._fail(
                    f"{path}.resources.{kind}",
                    "must map resource ids to operations",
                )
                continue

            for resource_id, operations in entries.items():
                entry_path = f"{path}.resources.{kind}.{resource_id}"
                if resource_id not in tool_resources[kind]:
                    self._fail(
                        entry_path,
                        "resource is not declared by the parent tool",
                    )
                if isinstance(operations, str):
                    operations = [operations]
                if not isinstance(operations, list) or not operations:
                    self._fail(
                        entry_path,
                        "operations must be one operation or a non-empty list",
                    )
                    continue
                unknown = [op for op in operations if op not in OPERATIONS]
                if unknown:
                    self._fail(entry_path, f"unknown operations: {unknown}")
                if len(set(operations)) != len(operations):
                    self._fail(entry_path, "duplicate operations")

    def _schedulable(self, schedulable: Any, path: str, function: dict) -> None:
        """A function marked as designed to run on a clock. Nobody
        watches such a run, so it has ceilings: no trust level to
        compare against, nobody to approve more, no model to think
        with. Cadence and inputs are the schedule's, not the manifest's."""
        if schedulable is None:
            return
        if not isinstance(schedulable, bool):
            self._fail(f"{path}.schedulable", "must be true or false")
            return
        if not schedulable:
            return

        level = function.get("permission_level")
        if level not in (0, 1):
            self._fail(
                f"{path}.schedulable",
                "a schedulable function must be permission_level 0 or 1 — "
                "no chat is behind a scheduled run, so nobody is there "
                "to approve more",
            )
        if function.get("llm") is True:
            self._fail(
                f"{path}.schedulable",
                "a schedulable function cannot declare llm: true — there "
                "is no chat whose model could answer",
            )

    def _function_authorization(
        self, authorization: Any, path: str, input_properties: set
    ) -> None:
        if authorization is None:
            return
        if not isinstance(authorization, dict):
            self._fail(f"{path}.authorization", "must be a mapping")
            return

        scopes = authorization.get("scopes", {})
        if scopes is None:
            scopes = {}
        if not isinstance(scopes, dict):
            self._fail(f"{path}.authorization.scopes", "must be a mapping")
            return

        for name, mapping in scopes.items():
            scope_path = f"{path}.authorization.scopes.{name}"
            if name not in self.scopes:
                self._fail(scope_path, "scope is not declared by the agent")
            if not isinstance(mapping, dict):
                self._fail(scope_path, "must be a mapping")
                continue
            from_input = mapping.get("from_input")
            if not self._is_str(from_input):
                self._fail(scope_path, "from_input is required")
            elif from_input not in input_properties:
                self._fail(
                    scope_path,
                    f"from_input '{from_input}' is not a property of inputs",
                )
            required = mapping.get("required", True)
            if not isinstance(required, bool):
                self._fail(scope_path, "required must be a boolean")

    # ------------------------------------------------------------------
    # JSON Schema subset
    # ------------------------------------------------------------------

    def _schema(self, schema: Any, path: str) -> set:
        """Validate an inputs/outputs schema; return its top-level property
        names (used by from_input checks)."""
        if not isinstance(schema, dict):
            self._fail(path, "a JSON Schema object is required")
            return set()
        if schema.get("type") != "object":
            self._fail(path, "root schema must have type object")
            return set()

        self._schema_node(schema, path, depth=1)

        properties = schema.get("properties")
        return set(properties) if isinstance(properties, dict) else set()

    def _schema_node(self, node: Any, path: str, depth: int) -> None:
        if not isinstance(node, dict):
            self._fail(path, "schema node must be a mapping")
            return
        if depth > MAX_SCHEMA_DEPTH:
            self._fail(path, f"schema nesting exceeds depth {MAX_SCHEMA_DEPTH}")
            return

        for keyword in node:
            if keyword in FORBIDDEN_KEYWORDS:
                self._fail(path, f"forbidden schema keyword '{keyword}'")
            elif keyword not in SCHEMA_KEYWORDS and not keyword.startswith("x-"):
                self._fail(path, f"unknown schema keyword '{keyword}'")

        declared_type = node.get("type")
        types = declared_type if isinstance(declared_type, list) else [declared_type]
        if declared_type is not None:
            for item in types:
                if item not in SCHEMA_TYPES:
                    self._fail(path, f"unknown schema type '{item}'")

        additional = node.get("additionalProperties")
        if additional is not None and not isinstance(additional, bool):
            self._fail(path, "additionalProperties must be a boolean")

        required = node.get("required")
        if required is not None and (
            not isinstance(required, list)
            or not all(self._is_str(r) for r in required)
        ):
            self._fail(path, "required must be a list of property names")

        self._x_resource(node, path)

        properties = node.get("properties")
        if properties is not None:
            if not isinstance(properties, dict):
                self._fail(path, "properties must be a mapping")
            else:
                for name, child in properties.items():
                    self._schema_node(child, f"{path}.{name}", depth + 1)

        items = node.get("items")
        if items is not None:
            self._schema_node(items, f"{path}.items", depth + 1)

    def _x_resource(self, node: dict, path: str) -> None:
        reference = node.get("x-resource")
        if reference is None:
            return
        if not isinstance(reference, dict):
            self._fail(path, "x-resource must be a mapping")
            return

        kind = X_RESOURCE_KINDS.get(reference.get("type"))
        if kind is None:
            self._fail(
                path, "x-resource type must be secret, data, or file"
            )
            return
        if reference.get("id") not in self.resources[kind]:
            self._fail(
                path,
                f"x-resource id '{reference.get('id')}' is not a declared "
                f"{reference.get('type')} resource",
            )

        declared_type = node.get("type")
        if declared_type != "string" and not (
            isinstance(declared_type, list) and "string" in declared_type
        ):
            self._fail(path, "x-resource requires a string-typed node")


# Typed access lives in the SDK (decentai_sdk/manifest.py) because agent
# code needs it inside a worker, where only the SDK is importable — one
# implementation, re-exported here so both sides of the boundary keep one
# import path for the whole contract.
from decentai_sdk.manifest import Manifest  # noqa: E402  (re-export)


def load_manifest(path: str | Path) -> Tuple[Optional[Manifest], List[str]]:
    """Read + validate one manifest file. Returns (manifest, errors) — the
    manifest is None unless the errors list is empty."""
    try:
        document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return None, [f"cannot read manifest: {exc}"]

    errors = ManifestValidator().validate(document)
    if errors:
        return None, errors
    return Manifest(document), []
