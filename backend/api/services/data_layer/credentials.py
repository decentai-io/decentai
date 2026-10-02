"""Credentials an agent asks for as it works (``call.credential``).

A browser agent lands on a login form it has never seen; an API agent
finds it needs a token for a host nobody declared. Neither is in the
manifest, because the platform did not know which sites the person
would send them to. So the shape is declared at run time: the agent
says which host and which fields, the platform derives a definition
under a ``site__<domain>`` family, and the person types the values on
a card in the chat. From then on the row is an ordinary secret — the
person's, shared through the same owner map as everything else, moved
to a successor as everything else is.

Three things make it different from a manifest-declared secret:

- **The family is the host's, not the agent's.** Every agent that asks
  for ``atlassian.com`` finds the same rows. Which agent may use one on
  which site is the person's say, recorded on the row as consent
  pairs, written when they answer a card and never assumed.
- **Fields accumulate.** A later ask for a field the family lacks adds
  a version with the union; the row keeps what it has; the card asks
  for what is missing.
- **A field may be asked every time** (``remember: false``): the
  definition knows it, the card asks for it on each use, and its value
  is never stored — a one-time code.

The value's road: a typed value goes from the card to ``save`` here,
into the vault, and the approval closes with the row's reference.
``resolve``, runtime-only under the chat's delegation, is the only door
that hands values out, and it audits every use as ``secret.use``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from database.stores import ApprovalStore, AuditStore
from database.stores.data.definitions import DefinitionStore
from database.stores.data.secrets import SecretStore
from server.authentication.policy import PolicyEngine
from server.custom_logging import CustomLoggerFactory
from server.governance import Sharing
from server.setup.app_state import get_runtime_clients
from util import utc_now


class SiteFamily:
    """How a host becomes a definition family, and what the platform
    adds to every such family."""

    PREFIX = "site__"
    #: Two-level public suffixes under which the registrable domain is
    #: three labels long. Not the whole public suffix list: the common
    #: ones, and a wrong guess costs one extra family, never a leak.
    TWO_LEVEL = {
        "co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au",
        "co.jp", "co.in", "co.nz", "co.za", "com.br", "com.mx", "com.sg",
        "com.hk", "com.tr", "com.ar", "com.sa", "com.eg", "co.kr", "com.cn",
    }
    HOST_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$")
    MAX_FIELDS = 20
    FIELD_TYPES = ("text", "secret")
    #: What the platform keeps on every site row, all plain: the host,
    #: the account a row is named by, its status, which agent may use
    #: it on which site, and where it has been used.
    PLATFORM_FIELDS = (
        {"name": "host", "label": "Host", "type": "string", "storage": "keys",
         "required": True, "options": [], "help": ""},
        {"name": "account", "label": "Account", "type": "string", "storage": "keys",
         "required": True, "options": [], "help": ""},
        {"name": "status", "label": "Status", "type": "select", "storage": "keys",
         "required": False, "options": ["saved", "needs_update"], "help": ""},
        {"name": "consents", "label": "Allowed", "type": "string", "storage": "keys",
         "required": False, "options": [], "help": "agent@site pairs, comma-joined"},
        {"name": "sites", "label": "Used on", "type": "string", "storage": "keys",
         "required": False, "options": [], "help": ""},
    )
    PLATFORM_NAMES = tuple(f["name"] for f in PLATFORM_FIELDS)
    BOOKKEEPING = ("status", "consents", "sites")

    @classmethod
    def domain(cls, raw: Any) -> str:
        """The registrable domain of a host or URL, lowercase, with a
        non-standard port kept — '' when it is not a host at all."""
        text = str(raw or "").strip().lower()
        if "://" in text:
            text = text.split("://", 1)[1]
        text = text.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
        if "@" in text:
            text = text.rsplit("@", 1)[1]
        host, _, port = text.partition(":")
        host = host.strip(".")
        if not host or not cls.HOST_PATTERN.match(host) or len(host) > 253:
            return ""
        labels = host.split(".")
        if len(labels) > 2 and ".".join(labels[-2:]) in cls.TWO_LEVEL:
            host = ".".join(labels[-3:])
        elif len(labels) > 2 and not re.match(r"^\d+\.\d+\.\d+\.\d+$", host):
            host = ".".join(labels[-2:])
        if port and port.isdigit() and port not in ("80", "443"):
            host = f"{host}:{port}"
        return host

    @classmethod
    def site(cls, raw: Any, domain: str) -> str:
        """The site a login is used on, as the agent saw it: host and
        path, no scheme or query — the domain when nothing better."""
        text = str(raw or "").strip().lower()
        if "://" in text:
            text = text.split("://", 1)[1]
        text = text.split("?", 1)[0].split("#", 1)[0].rstrip("/")
        if not text or "@" in text or len(text) > 200:
            return domain
        return text

    @classmethod
    def slug(cls, domain: str) -> str:
        return cls.PREFIX + re.sub(r"[^a-z0-9]", "_", domain)[:50]

    @classmethod
    def clean_fields(cls, raw: Any) -> List[Dict[str, Any]]:
        """The agent's fields, checked: a name, a label, text or secret,
        required unless said otherwise, remembered unless said
        otherwise. Platform names are the platform's."""
        if not isinstance(raw, list) or not raw or len(raw) > cls.MAX_FIELDS:
            raise ValueError(f"fields must be 1 to {cls.MAX_FIELDS} fields.")
        cleaned, seen = [], set()
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError("Each field must be an object.")
            name = str(item.get("name") or "").strip().lower()
            if not DefinitionStore.SLUG_PATTERN.match(name):
                raise ValueError(f"Field name {name!r} must be lowercase "
                                 f"letters, digits and underscores.")
            if name in cls.PLATFORM_NAMES and name != "account":
                raise ValueError(f"Field name {name!r} is the platform's.")
            if name in seen:
                raise ValueError(f"Duplicate field {name!r}.")
            seen.add(name)
            kind = str(item.get("type") or "text").strip().lower()
            if kind not in cls.FIELD_TYPES:
                raise ValueError(f"Field {name!r}: type must be text or secret.")
            cleaned.append({
                "name": name,
                "label": str(item.get("label") or "").strip()[:80] or name,
                "type": kind,
                "required": item.get("required", True) is not False,
                "remember": item.get("remember", True) is not False,
            })
        return cleaned

    @classmethod
    def definition_fields(cls, existing: List[Dict[str, Any]],
                          asked: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """The family's fields as the definition store takes them: the
        platform's, then every agent field ever asked, in order."""
        fields = [dict(f) for f in cls.PLATFORM_FIELDS]
        known = set(cls.PLATFORM_NAMES)
        for field in list(existing) + [cls.as_definition_field(f) for f in asked]:
            if field["name"] in known:
                continue
            known.add(field["name"])
            fields.append(dict(field))
        return fields

    @staticmethod
    def as_definition_field(field: Dict[str, Any]) -> Dict[str, Any]:
        secret = field["type"] == "secret"
        spec = {
            "name": field["name"], "label": field["label"],
            "type": "secret" if secret else "string",
            "storage": "values" if secret else "keys",
            # Required is the card's business, not the row's: a row may
            # hold a login and not yet the token a later ask wants.
            "required": False, "options": [], "help": "",
        }
        if not field.get("remember", True):
            spec["remember"] = False
        return spec


class CredentialController:
    """The three doors: resolve (runtime), save and allow (the person)."""

    #: How the consent pairs and the sites are kept on a row.
    SEP = ","

    def __init__(self):
        self.store = SecretStore()
        self.definitions = DefinitionStore()
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    @staticmethod
    def _payload(data):
        value = (data or {}).get("data")
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _owner(user):
        return {"org_id": str(user.get("org_id") or ""),
                "user_id": str(user.get("user_id") or "")}

    # -- resolve ---------------------------------------------------------
    async def resolve(self, data: dict, user: dict):
        """What the runtime does with an agent's ask: values when the
        row is there, allowed and complete; otherwise which card to
        put up. Runtime-only, under the chat's delegation, so the
        person's own visibility bounds every read."""
        if user.get("principal_type") != "runtime":
            return {"error": "Only the AI runtime resolves credentials."}, 403
        payload = self._payload(data)
        domain = SiteFamily.domain(payload.get("host"))
        if not domain:
            return {"error": "host must be a host name or a URL."}, 400
        agent_ref = str(payload.get("agent_ref") or "").strip()
        if not agent_ref:
            return {"error": "agent_ref is required."}, 400
        try:
            asked = SiteFamily.clean_fields(payload.get("fields"))
        except ValueError as exc:
            return {"error": str(exc)}, 400
        site = SiteFamily.site(payload.get("site"), domain)
        account = str(payload.get("account") or "").strip().lower()
        pinned = str(payload.get("resource_ref") or "").strip()
        refresh = bool(payload.get("refresh"))

        try:
            definition = self._family(user, domain, asked)
        except ValueError as exc:
            return {"error": str(exc)}, 400
        slug = definition["definition_id"]
        base = {"host": domain, "site": site, "definition_ref": definition["_id"],
                "resource_id": slug, "agent_ref": agent_ref}

        rows = self.store.list_visible(user, resource_id=slug)
        if pinned:
            rows = [r for r in rows if r.get("resource_ref") == pinned]
        elif account:
            rows = [r for r in rows
                    if str((r.get("keys") or {}).get("account") or "").lower() == account]
        if not rows:
            return {"status": "missing", **base, "account": account,
                    "fields": asked, "existing": False}, 200
        if len(rows) > 1:
            return {"status": "choose", **base, "instances": [
                {"resource_ref": r["resource_ref"], "name": r.get("name") or "",
                 "account": str((r.get("keys") or {}).get("account") or "")}
                for r in rows]}, 200
        row = rows[0]
        keys = dict(row.get("keys") or {})
        ref = str(row["resource_ref"])
        if self._pair(agent_ref, site) not in self._pairs(keys.get("consents")):
            return {"status": "consent", **base, "resource_ref": ref,
                    "account": str(keys.get("account") or ""),
                    "fields": asked}, 200

        try:
            stored = self.store.use(user, ref)
        except Exception as exc:
            self.logger.error(f"{ref} cannot be read: {exc}")
            return {"error": str(exc)}, 409
        held = {**keys, **stored}
        remembered = [f for f in asked if f["remember"]]
        missing = [f for f in remembered
                   if refresh or (f["required"] and not str(held.get(f["name"]) or "").strip())]
        if missing:
            return {"status": "missing", **base, "resource_ref": ref,
                    "account": str(keys.get("account") or ""),
                    "fields": missing, "existing": True}, 200

        AuditStore().append(
            "secret.use", user, chat_id=str(user.get("chat_id") or ""),
            resource_refs=[ref], details={"host": domain, "site": site,
                                          "agent_ref": agent_ref})
        values = {k: v for k, v in held.items() if k not in SiteFamily.BOOKKEEPING}
        return {"status": "ready", **base, "resource_ref": ref,
                "account": str(keys.get("account") or ""), "values": values,
                "ask": [f for f in asked if not f["remember"]]}, 200

    def _family(self, user: dict, domain: str, asked: List[Dict[str, Any]]) -> dict:
        """The host's definition, made at first ask and grown when an
        ask names a field it lacks."""
        org_id = str(user.get("org_id") or "")
        slug = SiteFamily.slug(domain)
        current = self.definitions.latest(org_id, slug)
        if current is None:
            return self.definitions.create_family(
                user, slug, f"{domain} login",
                description=f"A login for {domain}, asked for by an agent as it worked.",
                fields=SiteFamily.definition_fields([], asked))
        known = {f["name"] for f in current.get("fields") or []}
        if all(f["name"] in known for f in asked):
            return current
        return self.definitions.add_version(
            user, slug, current.get("label") or f"{domain} login",
            description=current.get("description") or "",
            fields=SiteFamily.definition_fields(current.get("fields") or [], asked))

    # -- the person's answers ----------------------------------------------
    async def save(self, data: dict, user: dict):
        """The entry card's answer: the typed values go into the vault,
        never onto the approval — which closes with the row's ref. A
        field asked every time rides the frame to the runtime and is
        not written anywhere."""
        payload = self._payload(data)
        approval, credential, refusal = self._pending(payload, user, "entry")
        if refusal:
            return refusal
        typed = payload.get("fields")
        if not isinstance(typed, dict):
            return {"error": "fields must be an object of name/value pairs."}, 400
        asked = credential.get("fields") or []
        remembered = {f["name"] for f in asked if f.get("remember", True)}
        once = {f["name"]: str(typed.get(f["name"]) or "")
                for f in asked if not f.get("remember", True)
                and str(typed.get(f["name"]) or "")}
        keep = {name: str(value) for name, value in typed.items()
                if name in remembered and str(value)}
        missing = [f["label"] for f in asked
                   if f.get("required", True) and f["name"] in remembered
                   and not str(typed.get(f["name"]) or "").strip()]
        if missing:
            return {"error": f"Required: {', '.join(missing)}."}, 400

        org_id = str(user.get("org_id") or "")
        definition = self.definitions.latest(org_id, str(credential.get("resource_id") or ""))
        if definition is None:
            return {"error": "The login's definition is gone."}, 409
        domain = str(credential.get("host") or "")
        site = str(credential.get("site") or domain)
        pair = self._pair(str(credential.get("agent_ref") or ""), site)
        ref = str(credential.get("resource_ref") or "")
        try:
            if ref:
                doc = self.store.visible_doc(user, ref)
                if doc is None:
                    return {"error": "The saved login is no longer visible to you."}, 404
                refusal = self._edit_refusal(user, doc)
                if refusal:
                    return refusal
                if doc.get("definition_ref") != definition["_id"]:
                    # Onto the family's newest shape first; migrate hands
                    # back the public view, so the row is read again.
                    self.store.migrate(doc, definition)
                    doc = self.store.visible_doc(user, ref)
                keys = dict(doc.get("keys") or {})
                fields = {**keep, "status": "saved",
                          "consents": self._join(self._pairs(keys.get("consents")) | {pair}),
                          "sites": self._join(self._pairs(keys.get("sites")) | {site})}
                row = self.store.update(doc, definition, fields=fields)
            else:
                account = str(keep.get("account") or credential.get("account") or "").strip()
                if not account:
                    account = next((keep[f["name"]] for f in asked
                                    if f.get("type", "text") == "text" and keep.get(f["name"])),
                                   domain)
                row = self.store.create(
                    user, definition, f"{domain} — {account}",
                    {"users": [str(user.get("user_id") or "")], "groups": []},
                    fields={**keep, "host": domain, "account": account,
                            "status": "saved", "consents": pair, "sites": site})
        except ValueError as exc:
            return {"error": str(exc)}, 400
        ref = str(row["resource_ref"])
        label = str(row.get("name") or domain)
        if not self._close(approval, ref, label, user):
            return {"error": "The card is already closed."}, 409
        AuditStore().append(
            "credential.saved", user, chat_id=str(approval.get("chat_id") or ""),
            resource_refs=[ref, str(approval["approval_id"])],
            details={"host": domain, "site": site,
                     "agent_ref": str(credential.get("agent_ref") or ""),
                     "fields": sorted(keep)})
        delivered = await get_runtime_clients().send(
            approval["chat_id"], user, {
                "event": "question_answered",
                "approval_id": str(approval["approval_id"]),
                "answer": {"resource_ref": ref, **({"once": once} if once else {})},
            })
        return {"resource_ref": ref, "delivered": bool(delivered)}, 200

    async def allow(self, data: dict, user: dict):
        """The consent card's yes: this agent may use this login on this
        site. Recorded on the row; the card closes; the runtime
        resolves again and finds it allowed."""
        payload = self._payload(data)
        approval, credential, refusal = self._pending(payload, user, "consent")
        if refusal:
            return refusal
        ref = str(credential.get("resource_ref") or "")
        doc = self.store.visible_doc(user, ref)
        if doc is None:
            return {"error": "The saved login is no longer visible to you."}, 404
        refusal = self._edit_refusal(user, doc)
        if refusal:
            return refusal
        definition = self.definitions.get_in(
            str(user.get("org_id") or ""), str(doc.get("definition_ref") or ""))
        if definition is None:
            return {"error": "The login's definition is gone."}, 409
        site = str(credential.get("site") or credential.get("host") or "")
        pair = self._pair(str(credential.get("agent_ref") or ""), site)
        keys = dict(doc.get("keys") or {})
        try:
            self.store.update(doc, definition, fields={
                "consents": self._join(self._pairs(keys.get("consents")) | {pair}),
                "sites": self._join(self._pairs(keys.get("sites")) | {site}),
            })
        except ValueError as exc:
            return {"error": str(exc)}, 400
        if not self._close(approval, "allow", "Allowed", user):
            return {"error": "The card is already closed."}, 409
        AuditStore().append(
            "credential.allowed", user, chat_id=str(approval.get("chat_id") or ""),
            resource_refs=[ref, str(approval["approval_id"])],
            details={"site": site, "agent_ref": str(credential.get("agent_ref") or "")})
        delivered = await get_runtime_clients().send(
            approval["chat_id"], user, {
                "event": "question_answered",
                "approval_id": str(approval["approval_id"]), "answer": "allow"})
        return {"resource_ref": ref, "delivered": bool(delivered)}, 200

    # -- helpers -------------------------------------------------------------
    @staticmethod
    def _edit_refusal(user: dict, doc: dict):
        """A login shared with someone is theirs to use, not to change:
        typing new values into it, or letting another agent use it, is
        its creator's — the rule every secret follows."""
        escape = PolicyEngine().is_allowed(user, "secrets:secret:set_owner_any")
        if Sharing.may_edit(user, doc, escape=escape):
            return None
        return {"error": "Only the person who saved this login can change "
                         "it or let another agent use it."}, 403

    def _pending(self, payload: dict, user: dict, mode: str):
        """The card this answer belongs to: the caller's own, still
        pending, a credential card of the expected mode."""
        if user.get("principal_type") == "runtime":
            return None, None, ({"error": "A runtime never answers its own card."}, 403)
        approval_id = str(payload.get("approval_id") or "")
        approval = ApprovalStore().owned(approval_id, self._owner(user))
        if approval is None:
            return None, None, ({"error": "Card not found."}, 404)
        if approval.get("status") != ApprovalStore.PENDING:
            return None, None, ({"error": f"The card is already {approval.get('status')}."}, 409)
        request = approval.get("request") or {}
        credential = request.get("credential") or {}
        if request.get("expects") != "credential" or credential.get("mode") != mode:
            return None, None, ({"error": f"This card does not take a {mode} answer."}, 400)
        return approval, credential, None

    @staticmethod
    def _close(approval: dict, answer: str, label: str, user: dict) -> bool:
        closed = ApprovalStore().decide(str(approval["approval_id"]), {
            "status": ApprovalStore.ANSWERED, "decision": "answer",
            "answer": answer, "answer_label": label,
            "resolved_at": utc_now(),
            "resolved_by": str(user.get("email") or ""),
        })
        if closed:
            AuditStore().append(
                "approval.resolved", user, chat_id=str(approval.get("chat_id") or ""),
                function=(approval.get("request") or {}).get("function"),
                resource_refs=[str(approval["approval_id"])],
                details={"decision": "answer", "kind": "question"})
        return closed

    @staticmethod
    def _pair(agent_ref: str, site: str) -> str:
        return f"{agent_ref}@{site}"

    @classmethod
    def _pairs(cls, raw: Any) -> set:
        return {part.strip() for part in str(raw or "").split(cls.SEP) if part.strip()}

    @classmethod
    def _join(cls, parts) -> str:
        return cls.SEP.join(sorted(parts))
