"""Every endpoint the doors serve: each area's controllers, and the rights the people's door checks on routes of its
own (api-server.md §3)."""

from __future__ import annotations

from ..agents.endpoints import AgentEndpoints, PackageEndpoints
from ..chats.endpoints import (ActivityEndpoints, CardEndpoints, ChatEndpoints, EventEndpoints, MessageEndpoints,
                               OutputEndpoints, StateEndpoints, SwitchEndpoints)
from ..chats.inspect import NetworkEndpoints, ProcessEndpoints, UseEndpoints
from ..chats.schedules import ScheduleEndpoints
from ..people.endpoints import (AuthEndpoints, GroupEndpoints, InvitationEndpoints, JoinEndpoints, KeyEndpoints,
                                MeEndpoints, OrganizationEndpoints, PasswordEndpoints, PolicyEndpoints,
                                ProfileEndpoints, UserEndpoints)
from ..people.permissions import Rules
from ..records.agents import AgentRecordEndpoints
from ..records.endpoints import DataEndpoints, FileEndpoints, SecretEndpoints
from ..records.files import PieceEndpoints
from ..records.oauth import OauthEndpoints
from ..runtimes.endpoints import AuthorityEndpoints, NodeEndpoints, ReportEndpoints
from ..settings.assistant import McpEndpoints, MemoryEndpoints, SkillEndpoints
from ..settings.models import ModelEndpoints
from ..settings.notify import NotificationEndpoints, OutboundEndpoints
from ..settings.oauth_apps import OauthAppEndpoints
from ..settings.organization import OrgSettingsEndpoints
from ..settings.speech import SpeechEndpoints
from ..settings.webhooks import WebhookEndpoints
from ..trail.trail import AuditEndpoints, Trail
from ..trail.usage import MonitorEndpoints, UsageEndpoints
from .callers import Callers
from .calls import Router

CONTROLLERS = (AuthEndpoints, MeEndpoints, PasswordEndpoints, JoinEndpoints, ProfileEndpoints, KeyEndpoints,
               OrganizationEndpoints, UserEndpoints, GroupEndpoints, PolicyEndpoints, InvitationEndpoints,
               AgentEndpoints, PackageEndpoints, SecretEndpoints, DataEndpoints, FileEndpoints, AgentRecordEndpoints,
               PieceEndpoints, OauthEndpoints, ChatEndpoints, MessageEndpoints, EventEndpoints, StateEndpoints,
               OutputEndpoints, ProcessEndpoints, NetworkEndpoints, UseEndpoints,
               CardEndpoints, ActivityEndpoints, SwitchEndpoints, ScheduleEndpoints, OrgSettingsEndpoints,
               ModelEndpoints, OauthAppEndpoints, MemoryEndpoints, SkillEndpoints, McpEndpoints,
               NotificationEndpoints, OutboundEndpoints, WebhookEndpoints, SpeechEndpoints, AuditEndpoints,
               UsageEndpoints, MonitorEndpoints, ReportEndpoints, NodeEndpoints, AuthorityEndpoints)
#: Rights that are no endpoint: the people's door checks the first three on routes of its own.
RIGHTS = {"files:file:upload": "Upload a file", "files:file:download": "Download a file",
          "settings:speech:transcribe": "Write speech down", "records:manage_any": "Reach any record of the organization"}


class Catalog:
    @staticmethod
    def router(callers: Callers) -> Router:
        """The router over every endpoint, and the catalog of actions a policy may name."""
        router = Router([controller() for controller in CONTROLLERS], callers, Rules(), Trail())
        Rules.words = {**{entry["action"]: entry["words"] for entry in router.catalog()}, **RIGHTS}
        Rules.catalog = sorted(Rules.words)
        return router
