"""One organization's approval of one package (docs/system/agent-code.md).

The package is shared: a thousand organizations approving the same
agent are one folder and one environment (library.py), so nothing true
of only one of them can be written onto it. This is where that lives
instead — built per chat from the contract, and thrown away with the
session.

An agent has two names, and they belong to different parties:

    agt_0ba09c35d8324040b6bd   what approval minted, org-scoped. The
                               contract, the grants, the resource
                               collections, the trace and the model's
                               choice of agent all speak this
    notebook                   what the package calls itself. Its
                               manifest, its code and its worker speak
                               this

Neither can be dropped. The mint is what stops one organization naming
another's agent; the package's own name is what its author wrote the
code against, and rewriting it per organization would change the bytes
and therefore the digest — one folder per organization again.

So everything outside the package speaks the ref, and the two places
that reach inside — the manifest lookup and the worker call — translate
with ``declared()``. Getting that backwards denies everything silently,
which is why the translation lives on the agent and nowhere else.
"""

from __future__ import annotations

from ai_runtime.agents.library import InstalledAgent


class ApprovedAgent(InstalledAgent):
    """The package as one approval addresses it: the same digest, manifest,
    folder and environment, answering to the approval's ref."""

    def __init__(self, package: InstalledAgent, agent_ref: str):
        super().__init__(package.digest, package.manifest, package.folder,
                         package.environment)
        self.agent_ref = str(agent_ref)

    @property
    def agent_id(self) -> str:
        """What the platform routes by. Not the package's own id — two
        organizations approving one package have two of these."""
        return self.agent_ref
