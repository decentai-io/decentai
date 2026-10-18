"""Who is an execution node: a credential, checked against the
authorities the Data Core holds (docs/system/execution-nodes.md).

    identity = Admission().admit(token)        or raises Refused(why)

A credential is admitted when, all four:

1. it is signed by an authority that is registered and not revoked —
   with the authority's public key, or with a key from the key set the
   authority's issuer publishes;
2. it is unexpired, for the Data Core, and lives minutes, not hours
   (``CREDENTIAL_MAX_SECONDS``);
3. its scope fits the authority's: a shared authority admits shared
   nodes only, and an organization's authority admits nodes for that
   organization only — a credential naming another organization, or
   none where the authority is shared, is refused;
4. the node is not refused by name.

What a node says of itself beyond its name, its organization and its
labels is not read here: a node can say anything, so a report is shown
and never decides.

Nothing is cached past a change: registering, revoking or refusing
moves ``generation`` and every remembered admission is thrown away,
which is what makes a revoke count on a node's next request.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import jwt

from contracts.node import (
    CREDENTIAL_ALGORITHMS, CREDENTIAL_AUDIENCE, CREDENTIAL_MAX_SECONDS,
    CREDENTIAL_TYPE)
from database.stores import AuthorityStore, NodeStore, RefusedNodeStore
from server.custom_logging import CustomLoggerFactory


class Refused(Exception):
    """Not admitted, and why, in words for a log and an operator."""


@dataclass(frozen=True)
class NodeIdentity:
    """An admitted node: its authority and its name together are its
    key, so two authorities naming a node alike name two nodes."""

    name: str
    authority_id: str
    #: ``shared``, or the one organization the node is for
    scope: str
    expires_at: float
    labels: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def key(self) -> str:
        return f"{self.authority_id}/{self.name}"

    @property
    def shared(self) -> bool:
        return self.scope == AuthorityStore.SHARED


class IssuerKeys:
    """The keys an issuer publishes, fetched from its address and kept
    for a few minutes; fetched again when a token names a key that is
    not among them — an issuer rotating its keys."""

    KEEP_SECONDS = 300
    #: The least time between two fetches of one address.
    AGAIN_SECONDS = 10

    def __init__(self):
        self._sets: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def key(self, url: str, kid: str):
        found = self._find(url, kid, fetch_if_stale=True)
        if found is None:
            with self._lock:
                fetched_at = self._sets.get(url, (0.0, None))[0]
            if time.monotonic() - fetched_at > self.AGAIN_SECONDS:
                found = self._find(url, kid, fetch_if_stale=True, force=True)
        return found

    def _find(self, url: str, kid: str, fetch_if_stale: bool,
              force: bool = False):
        with self._lock:
            held = self._sets.get(url)
        if force or held is None or (
                fetch_if_stale
                and time.monotonic() - held[0] > self.KEEP_SECONDS):
            held = (time.monotonic(), self._fetch(url))
            with self._lock:
                self._sets[url] = held
        keys = held[1]
        if keys is None:
            return None
        for key in keys.keys:
            if not kid or key.key_id == kid:
                return key
        return None

    @staticmethod
    def _fetch(url: str):
        import httpx

        try:
            answer = httpx.get(url, timeout=5.0)
            answer.raise_for_status()
            return jwt.PyJWKSet.from_dict(answer.json())
        except Exception:
            return None


class Admission:
    """The one place a node credential is checked. One for the
    process: it remembers admissions until their credential lapses or
    something changes."""

    #: Moves on every change to authorities or refusals, in this
    #: process (the Data Core is one process).
    generation = 0
    _remembered: Dict[str, Tuple[int, NodeIdentity]] = {}
    _issuers = IssuerKeys()
    _lock = threading.Lock()

    #: How far a credential's clock may be from the Data Core's.
    LEEWAY_SECONDS = 30

    def __init__(self):
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    @classmethod
    def changed(cls) -> None:
        """An authority was registered or revoked, or a node refused or
        let back: nothing admitted before counts."""
        with cls._lock:
            cls.generation += 1
            cls._remembered = {}

    # ------------------------------------------------------------------
    def admit(self, token: str, record: bool = True) -> NodeIdentity:
        token = str(token or "").strip()
        if not token:
            raise Refused("No node credential was presented.")
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._lock:
            held = self._remembered.get(digest)
        if held is not None and held[0] == self.generation \
                and held[1].expires_at > time.time():
            return held[1]
        since = self.generation

        identity = self._check(token)
        if identity.key in RefusedNodeStore().keys():
            raise Refused(f"The node {identity.key} is refused by name.")
        if record:
            try:
                NodeStore().seen(identity)
            except Exception as exc:  # the view, never the gate
                self.logger.warning(f"Node {identity.key} not recorded: {exc}")
        with self._lock:
            if since == self.generation:
                self._remembered[digest] = (since, identity)
        return identity

    def _check(self, token: str) -> NodeIdentity:
        try:
            header = jwt.get_unverified_header(token)
            unverified = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError:
            raise Refused("The node credential is not a signed token.")
        algorithm = str(header.get("alg") or "")
        if algorithm not in CREDENTIAL_ALGORITHMS:
            raise Refused(
                f"A node credential is signed with a key pair; '{algorithm}' "
                f"is not one the Data Core accepts.")
        kid = str(header.get("kid") or "")
        candidates = self._candidates(unverified, kid)
        if not candidates:
            raise Refused("No registered authority could have signed this "
                          "node credential.")

        last = "it is signed by no registered authority"
        for authority, key in candidates:
            try:
                claims = jwt.decode(
                    token, key, algorithms=[algorithm],
                    audience=CREDENTIAL_AUDIENCE,
                    leeway=self.LEEWAY_SECONDS,
                    options={"require": ["exp", "iat", "sub"]})
            except jwt.ExpiredSignatureError:
                raise Refused("The node credential has expired.")
            except jwt.InvalidAudienceError:
                raise Refused("The node credential is not for the Data Core.")
            except jwt.PyJWTError as exc:
                last = str(exc)
                continue
            return self._identity(authority, claims)
        raise Refused(f"The node credential was refused: {last}.")

    def _candidates(self, unverified: Dict[str, Any], kid: str
                    ) -> List[Tuple[Dict[str, Any], Any]]:
        """(authority, key) pairs that may have signed: the issuer the
        token names, or the key its header names, or every registered
        key — there are few, and each is tried once."""
        live = AuthorityStore().live()
        issuer = str(unverified.get("iss") or "")
        found: List[Tuple[Dict[str, Any], Any]] = []
        for authority in live:
            if authority.get("kind") == AuthorityStore.KIND_ISSUER:
                if issuer and issuer == authority.get("issuer"):
                    key = self._issuers.key(authority["jwks_url"], kid)
                    if key is not None:
                        found.append((authority, key.key))
        if found:
            return found
        keyed = [a for a in live if a.get("kind") == AuthorityStore.KIND_KEY]
        named = [a for a in keyed
                 if kid and kid in (a["_id"], a.get("fingerprint"))]
        return [(a, a["public_key"]) for a in (named or keyed)]

    def _identity(self, authority: Dict[str, Any],
                  claims: Dict[str, Any]) -> NodeIdentity:
        typ = claims.get("typ")
        if typ is not None and typ != CREDENTIAL_TYPE:
            raise Refused("The token is not a node credential.")
        issued, expires = claims.get("iat"), claims.get("exp")
        try:
            lifetime = float(expires) - float(issued)
        except (TypeError, ValueError):
            raise Refused("The node credential says no lifetime.")
        if lifetime > CREDENTIAL_MAX_SECONDS + self.LEEWAY_SECONDS:
            raise Refused(
                f"A node credential lives minutes: at most "
                f"{CREDENTIAL_MAX_SECONDS // 60}, and this one "
                f"{int(lifetime // 60)}.")
        name = str(claims.get("sub") or "").strip()
        if not name or len(name) > 200 or "/" in name:
            raise Refused("The node credential names no node.")

        scope = str(authority.get("scope") or "")
        said = str(claims.get("org") or "").strip()
        if scope == AuthorityStore.SHARED:
            if said and said != AuthorityStore.SHARED:
                raise Refused(
                    "A shared authority admits shared nodes only; this "
                    "credential names an organization.")
            node_scope = AuthorityStore.SHARED
        else:
            if said == AuthorityStore.SHARED:
                raise Refused(
                    "An organization's authority cannot admit a shared node.")
            if said and said != scope:
                raise Refused(
                    "The credential names an organization its authority "
                    "may not vouch for.")
            node_scope = scope

        labels = claims.get("labels")
        labels = tuple(sorted({str(label)[:64] for label in labels}))[:32] \
            if isinstance(labels, list) else ()
        return NodeIdentity(name=name, authority_id=authority["_id"],
                            scope=node_scope, expires_at=float(expires),
                            labels=labels)
