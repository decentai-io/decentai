"""The Note agent — the example: one agent, every feature the platform
can enforce (see manifest.yaml and README.md).

Stateless by design: every note, document and secret flows through the
mediated ``call.resources`` — the platform's data layer, scoped per call
to what the function declared — so the agent itself holds nothing. The
sync REMOTE stays simulated (no network), but its connection secret is
real. The tools hold no state of their own.

Copy this file, rename the class, and point `implementation.entrypoint`
at the new name.
"""

from decentai_sdk.base import AgentBase

from .tools import ArchiveTool, NotesTool, SyncTool


class NoteAgent(AgentBase):
    def tools(self):
        return [NotesTool(self), ArchiveTool(self), SyncTool(self)]
