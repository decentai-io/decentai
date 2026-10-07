"""What a person allowed for one call of a function that runs code.

A function that declares ``code: true`` puts its code before the person
(call.propose) with what the code needs. A card they allow is a grant
for that call and no longer:

- the hosts it named are open on the worker's way out until the call
  ends (egress.py), and
- the packages it named may be installed (environments.py ``extras``).

A card they did not allow grants nothing, and a function that did not
declare ``code: true`` is granted nothing by any card: there a card is
the person's consent and no more.

The names are checked before the card is shown, so that what a person
reads is what would be opened: a host is a name, never an address and
never every host under a name; a package is a name and a version.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ai_runtime.agents.environments import AgentEnvironment
from contracts.agent_manifest import ADDRESS_PATTERN, HOST_PATTERN


class CodeGrant:
    def __init__(self) -> None:
        #: Packages the person allowed on a card of this call, as the
        #: card wrote them.
        self.packages: set = set()
        #: The call's context, once it exists: where its worker is found.
        self.context: Any = None
        self._place: Any = None
        self._lent: list = []

    @classmethod
    def problem(cls, code: Dict[str, Any],
                listed: Optional[List[str]] = None) -> str:
        """Why a proposal cannot be granted as written, or ''.

        ``listed`` is the deployment's list of packages a program may
        install (Settings:Safety), or None where any may be named."""
        for host in code.get("hosts") or []:
            if not cls.host(host):
                return (f"'{host}' is not a host a person can allow: name "
                        f"one host, such as api.example.com, or "
                        f"api.example.com:8443")
        for package in code.get("packages") or []:
            # Read once, the way it will be installed: the name checked
            # against the list is the name handed to the installer, and
            # not another reading of the same words.
            asked = AgentEnvironment.requirement(package)
            if not asked:
                return (f"'{package}' is not a package a person can allow: "
                        f"name it, and its version where that matters — "
                        f"pandas, requests==2.32.3")
            if listed is not None and cls.package(asked) not in {
                    cls.package(name) for name in listed}:
                return (f"'{package}' is not on the list of packages a "
                        f"program may install here. The list is: "
                        f"{', '.join(sorted(listed)) or 'empty'}")
        return ""

    @staticmethod
    def package(requirement: str) -> str:
        """The name in a requirement, as an index compares names: no
        version, no extras, lower case, and ``_`` and ``.`` as ``-``."""
        name = re.split(r"[\[=<>!~\s]", str(requirement or "").strip(), 1)[0]
        return re.sub(r"[-_.]+", "-", name).lower()

    @staticmethod
    def host(written: str) -> str:
        """A host as a card may name it — ``name`` or ``name:port`` —
        or '' when it is not one."""
        written = str(written or "").strip().lower()
        name, colon, port = written.rpartition(":")
        if not colon:
            name, port = written, ""
        elif not (port.isdigit() and 1 <= int(port) <= 65535):
            return ""
        if (name.startswith("*.") or ADDRESS_PATTERN.match(name)
                or not HOST_PATTERN.match(name)):
            return ""
        return written

    def allow(self, code: Dict[str, Any]) -> None:
        """A card the person allowed: its hosts are opened, and its
        packages may be installed."""
        self.packages.update(str(p) for p in code.get("packages") or [])
        hosts = [self.host(h) for h in code.get("hosts") or []]
        place = getattr(getattr(self.context, "handle", None), "place", None)
        if place is not None and hosts:
            self._place = place
            self._lent.extend(place.lend(hosts))

    def allowed(self, packages: List[str]) -> Optional[str]:
        """The first of these the person did not allow, or None."""
        return next((p for p in packages if p not in self.packages), None)

    def close(self) -> None:
        """The call ended: what was opened for it is closed."""
        if self._place is not None:
            self._place.take_back(self._lent)
        self._place, self._lent = None, []
