"""The Notebook reference agent (see manifest.yaml).

Stateless by design: every note, document, and secret flows through the
mediated ``call.resources`` — the platform's data layer once
installed, so the agent itself holds nothing.
The sync REMOTE stays simulated (no network, no pip dependencies), but its
connection secret is real. The tools hold no state of their own.
"""

from decentai_sdk.base import AgentBase

from .tools import ArchiveTool, NoteTool, SyncTool


class NotebookAgent(AgentBase):
    def tools(self):
        return [NoteTool(self), ArchiveTool(self), SyncTool(self)]
