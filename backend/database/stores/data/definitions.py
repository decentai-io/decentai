"""Secret definitions — versioned templates every secret is created from.

A definition family is identified by its user-set slug (``definition_id``);
each saved change is an immutable new version (one document per version),
because instances record exactly which version they were validated against.
``split_fields`` is the bridge to the resource format: it validates instance
fields against a version and routes each into keys (plain) or values
(encrypted) per the field's ``storage``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from contracts.agent_manifest import (
    OAUTH_CHOICES, OAUTH_NAME_PATTERN, OAUTH_PATH_PATTERN,
)
from database.stores.agents import AgentManifestStore
from database.stores.base import MongoStore
from util import iso, utc_now


class DefinitionStore(MongoStore):
    COLLECTION = "secret_definitions"

    FIELD_TYPES = ("string", "number", "boolean", "select", "secret")
    STORAGES = ("keys", "values")
    MAX_FIELDS = 50
    SLUG_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,59}$")

    # ``name`` is the built-in instance label; a definition field may not
    # shadow it.
    RESERVED_FIELD_NAMES = ("name",)

    #: What a connected account's credential holds, added by the platform
    #: to every definition that carries an ``oauth`` block. None is
    #: required: the consent flow fills them, not a form.
    OAUTH_FIELDS = (
        {"name": "account", "label": "Account", "type": "string", "storage": "keys",
         "required": False, "options": [], "help": "Whose account this is, as the provider reports it."},
        {"name": "access_token", "label": "Access Token", "type": "secret", "storage": "values",
         "required": False, "options": [], "help": ""},
        {"name": "refresh_token", "label": "Refresh Token", "type": "secret", "storage": "values",
         "required": False, "options": [], "help": ""},
        {"name": "expires_at", "label": "Access Token Expires", "type": "string", "storage": "keys",
         "required": False, "options": [], "help": ""},
        {"name": "status", "label": "Status", "type": "select", "storage": "keys",
         "required": False, "options": ["connected", "needs_reconnect"], "help": ""},
    )
    OAUTH_FIELD_NAMES = tuple(field["name"] for field in OAUTH_FIELDS)
    PROVIDER_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,31}$")

    @classmethod
    def scope(cls, org_id: Any) -> Dict[str, Any]:
        """What one organization may see: its own definitions, and only
        those.

        There is no deployment-wide shelf. Platform credentials — the
        model key, the git token — used to live outside every
        organization, which made them a third kind of thing: readable
        everywhere, writable nowhere, and identified by a hardcoded
        list. Each now belongs to a domain that owns it outright
        (settings for connections, the source record for its token), so
        a definition here has one owner and one set of rules."""
        return {"org_id": str(org_id or "")}

    @staticmethod
    def to_public(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not doc:
            return None
        return {
            "definition_ref": doc["_id"],
            "definition_id": doc.get("definition_id", ""),
            "version": doc.get("version", 1),
            "label": doc.get("label", ""),
            "description": doc.get("description", ""),
            "fields": list(doc.get("fields") or []),
            "oauth": doc.get("oauth") or None,
            "created_at": iso(doc.get("created_at")),
        }

    # ------------------------------------------------------------------
    # Validating the definition itself
    # ------------------------------------------------------------------

    @classmethod
    def clean_slug(cls, raw: Any) -> str:
        slug = str(raw or "").strip().lower()
        if not cls.SLUG_PATTERN.match(slug):
            raise ValueError(
                "A definition id must be lowercase letters, digits and "
                "underscores, starting with a letter (2-60 characters)."
            )
        return slug

    @classmethod
    def clean_oauth(cls, raw: Any) -> Optional[Dict[str, Any]]:
        """The consent block, or None for a typed-in definition. The
        manifest contract has already refused the malformed; this is
        the store's own line so an organization-authored definition
        gets the same shape."""
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise ValueError("oauth must be an object.")
        provider = str(raw.get("provider") or "").strip().lower()
        if not cls.PROVIDER_PATTERN.match(provider):
            raise ValueError("oauth.provider must be a short lowercase id such as google.")
        cleaned: Dict[str, Any] = {"provider": provider}
        for key in ("authorize_url", "token_url"):
            value = str(raw.get(key) or "").strip()
            if not value.startswith("https://"):
                raise ValueError(f"oauth.{key} must be an https:// URL.")
            cleaned[key] = value
        scopes = [str(s).strip() for s in (raw.get("scopes") or []) if str(s).strip()]
        if not scopes:
            raise ValueError("oauth.scopes must name at least one scope.")
        cleaned["scopes"] = scopes
        params = raw.get("authorize_params") or {}
        if not isinstance(params, dict):
            raise ValueError("oauth.authorize_params must be an object.")
        cleaned["authorize_params"] = {str(k): str(v) for k, v in params.items()}
        # How this provider departs from the common shape. Only what a
        # block actually says is kept, so a Google or Microsoft block is
        # stored exactly as before and compares equal across versions.
        for key, allowed in OAUTH_CHOICES.items():
            if key in raw:
                if raw[key] not in allowed:
                    raise ValueError(f"oauth.{key} must be one of {list(allowed)}.")
                cleaned[key] = raw[key]
        if "scope_param" in raw:
            if not OAUTH_NAME_PATTERN.match(str(raw["scope_param"])):
                raise ValueError("oauth.scope_param must be a parameter name such as user_scope.")
            cleaned["scope_param"] = str(raw["scope_param"])
        if "token_path" in raw:
            if not OAUTH_PATH_PATTERN.match(str(raw["token_path"])):
                raise ValueError("oauth.token_path must be a dotted path such as authed_user.")
            cleaned["token_path"] = str(raw["token_path"])
        identity = raw.get("identity")
        if identity:
            cleaned["identity"] = cls._clean_identity(identity)
        return cleaned

    @classmethod
    def _clean_identity(cls, identity: Any) -> Dict[str, Any]:
        if not isinstance(identity, dict):
            raise ValueError("oauth.identity must be an object.")
        source = str(identity.get("source") or "url")
        if source not in ("url", "token"):
            raise ValueError("oauth.identity.source must be url or token.")
        field = str(identity.get("field") or "email").strip() or "email"
        if not OAUTH_PATH_PATTERN.match(field):
            raise ValueError("oauth.identity.field must be a dotted path such as email.")
        if source == "token":
            return {"source": "token", "field": field}
        if not str(identity.get("url") or "").startswith("https://"):
            raise ValueError("oauth.identity needs an https:// url.")
        cleaned: Dict[str, Any] = {"url": str(identity["url"]).strip(), "field": field}
        method = str(identity.get("method") or "GET").upper()
        if method not in ("GET", "POST"):
            raise ValueError("oauth.identity.method must be GET or POST.")
        if method != "GET":
            cleaned["method"] = method
        headers = identity.get("headers")
        if headers:
            if not isinstance(headers, dict):
                raise ValueError("oauth.identity.headers must be an object.")
            cleaned["headers"] = {str(k): str(v) for k, v in headers.items()}
        return cleaned

    @classmethod
    def clean_fields(cls, raw: Any, oauth: Any = None) -> List[Dict[str, Any]]:
        if oauth is not None and not raw:
            # A connected account may declare nothing of its own.
            return [dict(field) for field in cls.OAUTH_FIELDS]
        if not isinstance(raw, list) or not raw:
            raise ValueError("A definition needs at least one field.")
        if len(raw) > cls.MAX_FIELDS:
            raise ValueError(f"At most {cls.MAX_FIELDS} fields per definition.")

        cleaned: List[Dict[str, Any]] = []
        seen: set = set()
        for index, field in enumerate(raw, start=1):
            if not isinstance(field, dict):
                raise ValueError(f"Field {index} must be an object.")

            name = str(field.get("name") or "").strip()
            if not cls.SLUG_PATTERN.match(name):
                raise ValueError(
                    f"Field {index}: name must be lowercase letters, digits "
                    f"and underscores, starting with a letter."
                )
            if name in cls.RESERVED_FIELD_NAMES:
                raise ValueError(f"Field name '{name}' is reserved.")
            if oauth is not None and name in cls.OAUTH_FIELD_NAMES:
                raise ValueError(f"Field '{name}' is filled in by the OAuth flow.")
            if name in seen:
                raise ValueError(f"Duplicate field name '{name}'.")
            seen.add(name)

            field_type = str(field.get("type") or "").strip().lower()
            if field_type not in cls.FIELD_TYPES:
                raise ValueError(
                    f"Field '{name}': type must be one of "
                    f"{', '.join(cls.FIELD_TYPES)}."
                )

            # secret implies encrypted storage; others declare it.
            storage = "values" if field_type == "secret" else str(
                field.get("storage") or "keys"
            ).strip().lower()
            if storage not in cls.STORAGES:
                raise ValueError(f"Field '{name}': storage must be keys or values.")

            options: List[str] = []
            if field_type == "select":
                options = [
                    str(option).strip()
                    for option in (field.get("options") or [])
                    if str(option).strip()
                ]
                if not options:
                    raise ValueError(f"Field '{name}': a select needs options.")

            spec = {
                "name": name,
                "label": str(field.get("label") or "").strip() or name,
                "type": field_type,
                "storage": storage,
                "required": bool(field.get("required")),
                "options": options,
                "help": str(field.get("help") or "").strip(),
            }
            # A field asked every time and never stored (a one-time
            # code): kept only when said, so definitions written before
            # it existed compare unchanged.
            if field.get("remember") is False:
                spec["remember"] = False
            cleaned.append(spec)
        if oauth is not None:
            cleaned.extend(dict(field) for field in cls.OAUTH_FIELDS)
        return cleaned

    @classmethod
    def clean_content(
        cls, definition_id: Any, label: Any, description: Any, fields: Any,
        oauth: Any = None,
    ) -> Dict[str, Any]:
        oauth = cls.clean_oauth(oauth)
        return {
            "definition_id": cls.clean_slug(definition_id),
            "label": cls._clean_name(label, "A definition label"),
            "description": str(description or "").strip(),
            "fields": cls.clean_fields(fields, oauth=oauth),
            "oauth": oauth,
        }

    @classmethod
    def make_ref(cls, definition_id: Any, version: int, org_id: Any = "") -> str:
        """The document id for one version.

        Every definition carries its organization: a slug is chosen,
        another organization may choose it too, and a deployment-wide id
        would let whoever got there first take it from everybody else.
        """
        definition_id = cls.clean_slug(definition_id)
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("A definition version must be a positive integer.")
        org_id = str(org_id or "").strip()
        if not org_id:
            raise ValueError("A definition belongs to an organization.")
        return f"{org_id}:{definition_id}/v{version}"

    # ------------------------------------------------------------------
    # A slug and its version chain
    # ------------------------------------------------------------------

    def latest(self, org_id: str, definition_id: str) -> Optional[Dict[str, Any]]:
        return self.col.find_one(
            {**self.scope(org_id),
             "definition_id": str(definition_id or "").strip().lower()},
            sort=[("version", -1)],
        )

    def get_in(self, org_id: str, definition_ref: str) -> Optional[Dict[str, Any]]:
        """One version by ref, within reach of this organization."""
        return self.col.find_one({
            **self.scope(org_id), "_id": str(definition_ref or ""),
        })

    def list_latest(self, org_id: str) -> List[Dict[str, Any]]:
        """Newest version of every family this organization can reach."""
        latest: Dict[str, Dict[str, Any]] = {}
        for doc in self.col.find(self.scope(org_id)):
            slug = doc["definition_id"]
            if slug not in latest or doc["version"] > latest[slug]["version"]:
                latest[slug] = doc
        return sorted(latest.values(), key=lambda d: d["definition_id"])

    def create_family(
        self, author: Dict[str, Any], definition_id: Any, label: Any,
        description: Any = "", fields: Any = None, oauth: Any = None,
    ) -> Dict[str, Any]:
        content = self.clean_content(definition_id, label, description, fields, oauth)
        # Unique within the organization, not across organizations: a
        # slug is a name somebody chose, and two organizations installing
        # the same agent must not race for it.
        org_id = str(author.get("org_id") or "")
        if self.col.find_one({
            "org_id": org_id, "definition_id": content["definition_id"],
        }):
            raise ValueError("This definition id is not available.")
        return self._insert_version(author, content, version=1)

    def add_version(
        self, author: Dict[str, Any], definition_id: Any, label: Any,
        description: Any = "", fields: Any = None, oauth: Any = None,
    ) -> Dict[str, Any]:
        """A new immutable version — identical content is rejected."""
        content = self.clean_content(definition_id, label, description, fields, oauth)
        org_id = str(author.get("org_id") or "")
        current = self.latest(org_id, content["definition_id"])
        if current is None:
            raise ValueError("Unknown definition.")

        unchanged = all(
            current.get(key) == content[key]
            for key in ("label", "description", "fields", "oauth")
        )
        if unchanged:
            raise ValueError("Nothing changed — no new version created.")

        return self._insert_version(author, content, version=current["version"] + 1)

    def _insert_version(
        self, author: Dict[str, Any], content: Dict[str, Any], version: int
    ) -> Dict[str, Any]:
        org_id = str(author.get("org_id") or "")
        doc = {
            "_id": self.make_ref(content["definition_id"], version, org_id),
            **content,
            "org_id": org_id,
            "version": version,
            "created_by": str(author.get("user_id") or ""),
            "created_at": utc_now(),
        }
        return self._insert_unique(
            doc, "This definition version already exists."
        )

    def prune_unused(self, org_id: str, definition_id: str) -> int:
        """Delete every version of a family that nothing stands on — no
        secret created under it, no installed agent pinning it.

        With user authoring gone, a definition is an attribute of an
        agent's credential slot, and a version nothing references is not
        history: it is a shape that was superseded before anybody used
        it. Left alone they accumulate one per agent update, forever.
        When the last version goes, the family goes — which is what
        should happen to the shapes of an uninstalled agent nobody saved
        a credential for."""
        from database.stores.data.secrets import SecretStore

        org_id = str(org_id or "")
        slug = str(definition_id or "").strip().lower()
        counts = SecretStore().count_by_version(org_id, slug)
        pinned = set(AgentManifestStore()
                     .agents_pinning_secret_definition(org_id, slug).values())

        removed = 0
        for doc in self.versions(org_id, slug):
            version = doc.get("version", 1)
            if counts.get(version) or version in pinned:
                continue
            removed += self.col.delete_one({"_id": doc["_id"]}).deleted_count
        return removed

    def versions(self, org_id: str, definition_id: str) -> List[Dict[str, Any]]:
        """Every version of one family, oldest first."""
        return sorted(
            self.col.find({
                **self.scope(org_id),
                "definition_id": str(definition_id or "").strip().lower(),
            }),
            key=lambda doc: doc.get("version", 1),
        )

    # ------------------------------------------------------------------
    # Comparing two definitions' shapes
    # ------------------------------------------------------------------

    @staticmethod
    def shape_of(fields: Any) -> Dict[str, tuple]:
        """What a definition's fields MEAN, ignoring how they are worded.

        Name, type, storage, required and options are the contract: they
        decide what a filled-in credential contains and how it is sealed.
        Labels and help text are whoever wrote it first talking to a
        person, and two definitions that disagree only there describe the
        same credential."""
        return {
            field["name"]: (
                field.get("type"),
                field.get("storage", "values"),
                bool(field.get("required")),
                tuple(field.get("options") or []),
            )
            for field in fields or []
        }

    @classmethod
    def shape_mismatch(cls, left: Any, right: Any) -> list:
        """The field names on which two shapes disagree — empty when a
        credential of one really is a credential of the other.

        This is what gates handing an existing secret to an agent: the
        agent declared what it expects to receive, and a credential of a
        different shape is not that, however plausible the name."""
        ours, theirs = cls.shape_of(left), cls.shape_of(right)
        return sorted(
            set(ours) ^ set(theirs)
            | {name for name in set(ours) & set(theirs)
               if ours[name] != theirs[name]}
        )

    # ------------------------------------------------------------------
    # Instance validation — fields → (keys, values) per one version
    # ------------------------------------------------------------------

    @classmethod
    def split_fields(
        cls, definition: Dict[str, Any], fields: Any, partial: bool = False
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Validate instance fields against a definition version and route
        them into (keys, values). ``partial`` skips the required check —
        updates validate only what they touch."""
        if fields is None:
            fields = {}
        if not isinstance(fields, dict):
            raise ValueError("Fields must be an object of name/value pairs.")

        spec = {field["name"]: field for field in definition.get("fields") or []}

        unknown = [name for name in fields if name not in spec]
        if unknown:
            raise ValueError(f"Unknown fields: {', '.join(sorted(unknown))}.")

        keys: Dict[str, Any] = {}
        values: Dict[str, Any] = {}
        for name, value in fields.items():
            cleaned = cls._clean_field_value(spec[name], value)
            (values if spec[name]["storage"] == "values" else keys)[name] = cleaned

        if not partial:
            missing = [
                field["label"] for field in spec.values()
                if field["required"] and not cls._provided(field, fields.get(field["name"]))
            ]
            if missing:
                raise ValueError(f"Required: {', '.join(missing)}.")

        return keys, values

    @staticmethod
    def _provided(field: Dict[str, Any], value: Any) -> bool:
        if value is None:
            return False
        if field["type"] in ("string", "secret", "select") and str(value).strip() == "":
            return False
        return True

    @staticmethod
    def _clean_field_value(field: Dict[str, Any], value: Any) -> Any:
        name, field_type = field["label"], field["type"]

        if field_type in ("string", "secret"):
            if not isinstance(value, str):
                raise ValueError(f"{name} must be text.")
            return value.strip() if field_type == "string" else value
        if field_type == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a number.")
            return value
        if field_type == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be true or false.")
            return value
        if field_type == "select":
            value = str(value or "").strip()
            if value not in field["options"]:
                raise ValueError(
                    f"{name} must be one of: {', '.join(field['options'])}."
                )
            return value
        # Manifest data resources may declare structured payloads; the
        # authored-definition editor never offers this type, so secrets
        # are untouched by it.
        if field_type == "object":
            if not isinstance(value, dict):
                raise ValueError(f"{name} must be an object.")
            return value
        raise ValueError(f"{name}: unsupported field type.")
