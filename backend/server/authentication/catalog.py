"""The access vocabulary: what may be granted, and to whom.

Data, not logic — the three lists this platform's authorization is written
in, kept apart from the engine that evaluates them (``policy.py``) and the
gateway that applies them (``access.py``).

Deliberately Python in the repository rather than a file a deployment can
edit. Every entry here is a security boundary: widening ``RUNTIME_ENDPOINTS``
hands a delegated model a new door, and adding to ``BASELINE_ACTIONS`` grants
something to everyone who exists. Those belong in a code review and a
deployment, not in a text file that can be changed underneath a running
process — and a malformed edit should be impossible rather than caught at
boot.

    BASELINE_ACTIONS     what every user may do without anyone granting it
    ACTION_CATALOG       the whole grantable vocabulary, grouped for the editor
    RUNTIME_ENDPOINTS    the delegation surface a runtime principal is fenced to
"""

from __future__ import annotations

from typing import Any, Dict


# What every user may do without anyone granting it: their own account,
# and the memory the assistant keeps ABOUT them. Reading, correcting and
# deleting a personal record is not a privilege to hand out — a profile
# nobody can correct is the thing this platform refuses to build.
#
# Seeded into BaseAccess by bootstrap/init_db.py. It lives beside the
# catalog that makes an action grantable at all, so the deployment seed
# and the test harness read the same list.
BASELINE_ACTIONS = (
    "account:profile:get",
    "account:profile:update",
    # The colleagues you already share a group with, for "share with a
    # person" pickers. It is the reach of the caller's own memberships,
    # never a directory of the organization.
    "account:profile:peers",
    # The whole loop, or none of it. `create` is the assistant's write
    # during a chat, and a delegated runtime carries the USER's
    # permissions — so without it here, nothing is ever remembered for
    # anyone but an administrator, and the other three grant a view of a
    # permanently empty page. A deployment that wants no memory at all
    # unticks these; that is a decision, not an oversight.
    "settings:memory:create",
    "settings:memory:list",
    "settings:memory:update",
    "settings:memory:delete",
    # Their own API keys: a standing credential that is the person,
    # for scripts. Making one grants nothing the person lacks.
    "settings:apikey:list",
    "settings:apikey:create",
    "settings:apikey:revoke",
    # Their own audit trail: what agents did for them, in their own
    # chats. Reading it is not a privilege to hand out; the
    # organization's whole trail is (ai:audit:list_all).
    "ai:audit:list",
    # Handing their own things to a colleague. Each transfer is bounded
    # by ownership — only what they created — so this grants nothing
    # about anyone else's.
    "secrets:secret:transfer",
    "data:record:transfer",
    # Connecting one of their own accounts: it ends in a credential
    # they create, so it grants nothing beyond secrets:secret:create.
    "secrets:oauth:start",
    "files:file:transfer",
    "skills:skill:transfer",
    # What their own chats set in motion — schedules, cards, background
    # work — across every chat. Their records; reading them is not a
    # privilege to hand out.
    "ai:activity:list",
    # Their own switch: stopping everything of theirs, and letting it
    # run again. It reaches nobody else's work, so it is nobody's to
    # grant or withhold.
    "ai:switch:status",
    "ai:switch:stop",
    "ai:switch:resume",
    # Their own clock: a person may put a reminder or a schedulable
    # function on it, pause it, and take it off, from the page as well
    # as through a chat.
    "ai:schedule:functions",
    "ai:schedule:create",
    "ai:schedule:update",
    "ai:schedule:delete",
    # Reading the organization's own knowledge. The runtime carries the
    # user's permissions, so without these a chat cannot see the skills
    # catalog at all — the assistant would simply not know the things
    # the company took the trouble to write down. AUTHORING stays a
    # grant: writing for everyone is not a baseline act.
    "skills:skill:list",
    "skills:skill:get",
    # Their own MCP servers: remote tool servers a person adds for
    # their own chats, reached with their own credential. Nothing of
    # anybody else's is in reach, and the deployment can switch the
    # whole feature off (Settings:Safety). `use` is the runtime's read
    # of where a server is, under the person's delegation.
    "mcp:server:list",
    "mcp:server:get",
    "mcp:server:create",
    "mcp:server:update",
    "mcp:server:refresh",
    "mcp:server:delete",
    "mcp:server:use",
    # The runtime's pull door (docs/system/agent-code.md): the exact
    # bytes this organization approved for an agent, so a runtime that
    # lacks them can materialize approved code under the connected
    # chat's own delegation. Baseline for the same reason the skills
    # catalog is — the runtime carries the USER's permissions, and a
    # chat whose person needed a grant to fetch code would serve agents
    # for administrators and nobody else.
    "agents:agent:fetch_package",
    # Its cleanup read: which digests anybody still pins, so the runtime
    # can delete code nobody approves. Baseline for the same reason —
    # whichever person's chat is up is the one the sweep runs under.
    "agents:agent:pinned_digests",
    # The runtime's word that an agent's code is ready on it. Baseline
    # for the same reason as the pull door: whichever person's chat
    # happened to build it is the one whose delegation reports it.
    "agents:agent:prepared",
    # Trying an agent on the data it ships: the rows become the person's
    # own records and files, which they may already create and delete.
    "agents:agent:samples",
    "agents:agent:loadsamples",
    "agents:agent:removesamples",
    # Naming which of your own visible credentials answers when several
    # could. A personal preference about personal records; gating it
    # would only produce chats that fail with "ambiguous" for people
    # nobody thought to grant a settings switch to.
    "secrets:secret:set_default",
    # A login an agent asked for as it worked: typing it in, and
    # allowing an agent to use a saved one — the person's own cards.
    "secrets:credential:save",
    "secrets:credential:allow",
    # Reading the SHAPE of a credential — its field names and which of
    # them are encrypted. Not a value, and the form that asks a person
    # for their own password cannot be drawn without it; gating this
    # produces an empty form rather than a refusal, which is worse.
    "secrets:definition:get",
    # Reading which models exist — names and providers, never keys. The
    # chat config and the preferences page are drawn from this list for
    # every person who can open a chat, so gating it produces chats that
    # cannot say what they would think with.
    "settings:llm:list",
    # Speaking a message instead of typing it — the composer's
    # microphone, for whoever may write a message at all.
    "settings:speech:transcribe",
    # Being reached when a chat needs you: every person's own devices
    # and their own choice about email. Nothing here reaches anyone else.
    "settings:notifications:get",
    "settings:notifications:subscribe",
    "settings:notifications:unsubscribe",
    "settings:notifications:update",
    "settings:notifications:test",
)

# Bump when BASELINE_ACTIONS gains an entry. The revision recorded on the
# policy is what makes a new action arrive EXACTLY ONCE: administrators
# are expected to edit BaseAccess — unticking an action revokes it for
# everybody — and a seed that re-asserted the whole list on every run
# would quietly undo that on the next restart.
BASELINE_REVISION = 25


# What the seeded Members group grants: the platform's everyday use, for
# a person who is not its administrator — their own chats and what the
# assistant does in them on their behalf (a delegated runtime carries the
# person's permissions, so its own doors are here too), their own files,
# saved data and credentials, and seeing the agents they were given.
# Installing agents, the organization's settings and other people are not
# in it. A group like any other afterwards: an administrator edits it, or
# removes it, and it is not put back.
MEMBER_ACTIONS = (
    "ai:chat:*", "ai:message:*", "ai:state:*", "ai:event:*",
    "ai:storage:*", "ai:approval:*", "ai:schedule:*",
    "ai:audit:record",
    "settings:llm:use",
    "files:file:list", "files:file:get", "files:file:upload",
    "files:file:download", "files:file:update", "files:file:delete",
    "data:record:list", "data:record:get", "data:record:shapes",
    "data:record:create", "data:record:update", "data:record:delete",
    "secrets:secret:list", "secrets:secret:get", "secrets:secret:create",
    "secrets:secret:update", "secrets:secret:delete", "secrets:secret:use",
    "secrets:secret:instances",
    "secrets:credential:resolve", "secrets:definition:list",
    "agents:agent:list", "agents:agent:get",
    "agents:agent:secretgrants", "agents:agent:secretgrant",
    "agents:agent:secretrevoke", "agents:agent:secretlendable",
    "agents:agent:secretlendmany",
)
#: The name the Members group is seeded under, and looked up by where a
#: new person is offered it.
MEMBERS_GROUP = "Members"


# An action IS an endpoint (``iam:user:set_groups``); this lists what
# exists and drives the policy editor — adding a line here is what makes
# an endpoint grantable.
#
# What is NOT here: an installed agent's functions. Those are discovered
# rather than authored, so they live in their own collection keyed by the
# agent (see AgentGrantStore) and are removed with it. This is the
# platform's fixed vocabulary — including the actions that INSTALL an
# agent and hand out access to one.
ACTION_CATALOG: Dict[str, Dict[str, Any]] = {
    "account:profile": {
        "label": "Own account",
        "actions": {
            "account:profile:get": "View your own profile",
            "account:profile:update": "Edit your own name",
            "account:profile:peers":
                "List the people who share a group with you",
        },
    },
    "iam:organization": {
        "label": "Organization",
        "actions": {
            "iam:organization:get": "View organization details",
            "iam:organization:rename": "Rename the organization",
        },
    },
    "iam:user": {
        "label": "Users",
        "actions": {
            "iam:user:list": "View users",
            "iam:user:get": "View one user",
            "iam:user:create": (
                "Add a person and hand them a temporary password, where "
                "no invitation can be sent"
            ),
            "iam:user:reset_password": (
                "Give a person who forgot their password a temporary one"
            ),
            "iam:user:update": "Edit a user's profile",
            "iam:user:set_groups": "Place a user into groups",
            "iam:user:set_status": "Disable or re-enable a user",
            "iam:user:delete": "Remove a user, handing what they own to a successor",
            "iam:user:leaving": (
                "Preview what a user owns — chats, credentials, records, "
                "files, skills, connections, sources — before removing them"
            ),
        },
    },
    "iam:invitation": {
        "label": "Invitations",
        "actions": {
            "iam:invitation:list": "View pending invitations",
            "iam:invitation:create": "Invite users",
            "iam:invitation:revoke": "Revoke a pending invitation",
        },
    },
    "iam:group": {
        "label": "Groups",
        "actions": {
            "iam:group:list": "View groups",
            "iam:group:get": "View one group and its members",
            "iam:group:create": "Create groups",
            "iam:group:rename": "Rename a group",
            "iam:group:set_roles": "Attach roles to a group",
            "iam:group:delete": "Delete a group",
        },
    },
    "iam:role": {
        "label": "Roles",
        "actions": {
            "iam:role:list": "View roles",
            "iam:role:get": "View one role",
            "iam:role:create": "Create roles",
            "iam:role:update": "Rename a role",
            "iam:role:set_policies": "Attach policies to a role",
            "iam:role:delete": "Delete roles",
        },
    },
    "iam:policy": {
        "label": "Policies",
        "actions": {
            "iam:policy:list": "View policies",
            "iam:policy:get": "View one policy and its permissions",
            "iam:policy:create": "Create policies",
            "iam:policy:update": "Edit a policy",
            "iam:policy:delete": "Delete policies",
        },
    },
    # The data layer. Holding these reaches each domain's API; WHICH
    # documents come back is decided by each document's owner, not by
    # policy. The encrypted values are not readable through any of them.
    "secrets:credential": {
        "label": "Logins asked for by agents",
        "actions": {
            "secrets:credential:save": (
                "Type in a login an agent asks for as it works, saved "
                "as one of your secrets"
            ),
            "secrets:credential:allow": (
                "Let an agent use one of your saved logins on a site"
            ),
            # The engine's read, on the caller's behalf: which card to
            # put up, or the values when the login is there and allowed.
            "secrets:credential:resolve": (
                "Let your chat's engine use the logins you saved for "
                "the sites its agents work on"
            ),
        },
    },
    "secrets:secret": {
        "label": "Secrets",
        "actions": {
            "secrets:secret:list": "View the secrets shared with you",
            "secrets:secret:get": "View one secret's details",
            "secrets:secret:create": "Create secrets",
            "secrets:secret:update": "Edit a secret you created",
            "secrets:secret:delete": "Delete a secret you created",
            "secrets:secret:set_default": (
                "Choose which of your credentials answers by default"
            ),
            # The engine's own read, on the caller's behalf — including
            # the chat's model key, without which no chat can start. It
            # is listed so a least-privileged chat user can be granted
            # exactly this; the controller still refuses anyone but a
            # delegated runtime, so holding it does not expose a value
            # to the person.
            "secrets:secret:use": (
                "Let your chat's engine use a credential you can see — "
                "the model key a chat needs to start, and the "
                "credentials its agents were given"
            ),
            "secrets:secret:transfer": "Hand one of your credentials to another member",
            # The engine's read of WHICH credentials a slot may use —
            # names and accounts, never values — so an agent can offer
            # the person a choice among several. Runtime-only at the
            # controller, like use.
            "secrets:secret:instances": (
                "Let your chat's engine list which of your credentials "
                "an agent may use, by name"
            ),
            "secrets:secret:set_owner_any": (
                "Share secrets organization-wide or with any group or "
                "user, and maintain secrets created by others"
            ),
        },
    },
    "secrets:oauth": {
        "label": "Connected accounts",
        "actions": {
            "secrets:oauth:start": (
                "Connect an account by signing in with the provider, "
                "instead of typing a credential"
            ),
        },
    },
    "secrets:definition": {
        "label": "Secret types",
        "actions": {
            "secrets:definition:list": "View the secret types",
            "secrets:definition:get": "View one secret type",
        },
    },
    "settings:notifications": {
        "label": "Notifications",
        "actions": {
            "settings:notifications:get": "See how you are reached when a chat needs you",
            "settings:notifications:subscribe": "Let this device receive notifications",
            "settings:notifications:unsubscribe": "Stop notifications on a device",
            "settings:notifications:update": "Choose whether an email may follow",
            "settings:notifications:test": "Send yourself a test notification",
        },
    },
    "settings:speech": {
        "label": "Speech to text",
        "actions": {
            "settings:speech:get": "See which model writes spoken messages down",
            "settings:speech:update": "Choose the transcription model",
            # Baseline: anyone who can write a message may speak one.
            "settings:speech:transcribe": (
                "Speak a message instead of typing it"
            ),
        },
    },
    "settings:safety": {
        "label": "Safety",
        "actions": {
            "settings:safety:get": (
                "See what agents may do without asking"
            ),
            "settings:safety:update": (
                "Choose the sites no agent may open, how often scripts "
                "and programs ask, and the packages a program may install"
            ),
        },
    },
    "settings:routing": {
        "label": "Agent routing",
        "actions": {
            "settings:routing:get": (
                "See how chats find the right agent among many"
            ),
            "settings:routing:update": (
                "Choose the embedding model and the routing numbers"
            ),
        },
    },
    "settings:llm": {
        "label": "LLM connections",
        "actions": {
            "settings:llm:use": (
                "Let your chat's engine read the model key of a "
                "connection you can see"
            ),
            "settings:llm:list": "View the organization's LLM connections",
            "settings:llm:create": "Add an LLM connection",
            "settings:llm:update": "Edit an LLM connection or rotate its key",
            "settings:llm:setdefault": "Choose the default LLM connection",
            "settings:llm:delete": "Remove an LLM connection",
            "settings:llm:transfer": "Hand one of your LLM connections to another member",
            "settings:llm:manage_any": (
                "Edit, rotate, remove and transfer any of the organization's "
                "LLM connections, not only your own"
            ),
        },
    },
    "data:record": {
        "label": "Agent data",
        "actions": {
            "data:record:list": "View the agent records shared with you",
            "data:record:get": "View one record's details",
            "data:record:shapes": (
                "See which record types installed agents declare — the "
                "shapes a new record can be created in"
            ),
            "data:record:create": "Create agent records",
            "data:record:update": "Edit a record you created",
            "data:record:delete": "Delete a record you created",
            "data:record:transfer": "Hand one of your records to another member",
            "data:record:set_owner_any": (
                "Share records organization-wide or with any group or "
                "user, and maintain records created by others"
            ),
        },
    },
    "settings:memory": {
        "label": "Memory",
        "actions": {
            "settings:memory:list": "See what the assistant remembers about you",
            "settings:memory:create": (
                "Let a chat save a memory — the assistant's own write, "
                "shown in the conversation as it happens"
            ),
            "settings:memory:update": "Correct something the assistant remembers",
            "settings:memory:delete": "Delete something the assistant remembers",
        },
    },
    "settings:oauth": {
        "label": "Connected apps",
        "actions": {
            "settings:oauth:list": (
                "See which provider apps the organization registered, "
                "and the redirect URI to give a provider"
            ),
            "settings:oauth:create": "Register a provider app (client id and secret)",
            "settings:oauth:update": "Rotate a provider app's id or secret",
            "settings:oauth:delete": "Remove a provider app registration",
        },
    },
    "settings:apikey": {
        "label": "API keys",
        "actions": {
            "settings:apikey:list": "See your API keys — names and dates, never the secret",
            "settings:apikey:create": "Make an API key that acts as you, for scripts",
            "settings:apikey:revoke": "Revoke one of your API keys",
        },
    },
    "skills:skill": {
        "label": "Skills",
        "actions": {
            "skills:skill:list": "View the skills shared with you",
            "skills:skill:get": "Read one skill's full body",
            "skills:skill:create": "Create skills",
            "skills:skill:update": "Edit a skill you created",
            "skills:skill:delete": "Delete a skill you created",
            "skills:skill:transfer": "Hand one of your skills to another member",
            "skills:skill:set_owner_any": (
                "Share skills organization-wide or with any group or "
                "user, and maintain skills created by others"
            ),
        },
    },
    "mcp:server": {
        "label": "MCP servers",
        "actions": {
            "mcp:server:list": "See the MCP servers you added",
            "mcp:server:get": "See one of your MCP servers and its tools",
            "mcp:server:create": "Add an MCP server for your own chats",
            "mcp:server:update": (
                "Switch one of your MCP servers or its tools on or off, "
                "and set what each tool costs to call"
            ),
            "mcp:server:refresh": "Read again what one of your MCP servers offers",
            "mcp:server:delete": "Remove one of your MCP servers",
            "mcp:server:use": "Your assistant reaches one of your MCP servers",
        },
    },
    "files:file": {
        "label": "Files",
        "actions": {
            "files:file:list": "View the files shared with you",
            "files:file:get": "View one file's details",
            "files:file:upload": "Upload files",
            "files:file:download": "Download files shared with you",
            "files:file:update": "Edit a file you uploaded",
            "files:file:delete": "Delete a file you uploaded",
            "files:file:transfer": "Hand one of your files to another member",
            "files:file:set_owner_any": (
                "Share files organization-wide or with any group or "
                "user, and maintain files uploaded by others"
            ),
        },
    },
    # The AI domain. A chat needs more than the chat service: the
    # runtime acts inside it under the USER'S OWN policy (it is the
    # same principal, fenced to the delegation surface), so a person
    # who may chat must also be allowed the writes their engine makes
    # for them. Those actions say so, and no policy can hand them to
    # a user directly — the controllers refuse a non-runtime caller.
    "ai:chat": {
        "label": "AI chat",
        "actions": {
            "ai:chat:create": "Create chats",
            "ai:chat:open": (
                "Open a chat session — create or resume it with the "
                "model, agents and permissions resolved in one answer"
            ),
            "ai:chat:get": "Read owned chats",
            "ai:chat:list": "List owned chats",
            "ai:chat:update": "Update owned chats",
            "ai:chat:title": "Your assistant names a chat from its content",
            "ai:chat:archive": "Archive owned chats",
            "ai:chat:restore": "Restore archived chats",
            "ai:chat:delete": "Delete owned chats",
            "ai:chat:sendmessage": "Send chat messages",
            "ai:chat:stop": "Stop what your assistant is doing",
            "ai:chat:uploadfile": "Upload files to owned chats",
            "ai:chat:plan": (
                "Your engine records what a turn set out to do"
            ),
            "ai:chat:contract": (
                "Your assistant asks what this chat may do — the model, "
                "the agents, the grants, the trust level"
            ),
        },
    },
    "ai:state": {
        "label": "AI assistant state",
        "actions": {
            "ai:state:get": "Your assistant reads its own mind back",
            "ai:state:save": "Your assistant persists its mind, every beat",
        },
    },
    "ai:activity": {
        "label": "Activity",
        "actions": {
            "ai:activity:list": (
                "See your schedules, pending approvals and background "
                "work across every chat"
            ),
        },
    },
    "ai:switch": {
        "label": "Stop everything",
        "actions": {
            "ai:switch:status": "See whether you have stopped everything",
            "ai:switch:stop": (
                "Stop everything your chats are doing, at once, and "
                "hold it stopped"
            ),
            "ai:switch:resume": "Let your chats run again",
        },
    },
    "ai:approval": {
        "label": "AI approvals",
        "actions": {
            "ai:approval:list": "See a chat's pending approval cards",
            "ai:approval:decide": "Approve or deny what your assistant asked",
            "ai:approval:open": "Your assistant asks for your approval",
            "ai:approval:expire": "Your assistant closes a question nobody answered",
        },
    },
    "ai:schedule": {
        "label": "AI schedules",
        "actions": {
            "ai:schedule:load": "Your assistant loads its reminders and "
                                "scheduled checks",
            "ai:schedule:add": "Your assistant sets a reminder or a "
                               "scheduled check",
            "ai:schedule:ran": "Your assistant records what a reminder "
                               "or a scheduled check did",
            "ai:schedule:remove": "Your assistant takes a reminder or a "
                                  "scheduled check off the clock",
            "ai:schedule:functions": (
                "See which of your functions may run on a schedule"
            ),
            "ai:schedule:create": (
                "Put a reminder or a schedulable function on your own "
                "clock, from the page"
            ),
            "ai:schedule:update": "Pause or resume one of your schedules",
            "ai:schedule:delete": "Take a schedule off your clock",
        },
    },
    "ai:message": {
        "label": "AI messages",
        "actions": {
            "ai:message:list": "Read a chat's messages",
            "ai:message:create": "Your engine records a chat message",
        },
    },
    "ai:event": {
        "label": "AI events",
        "actions": {
            "ai:event:list": "Replay a chat's events after a disconnect",
            "ai:event:append": "Your engine records what it is doing",
            "ai:event:record": "Your assistant records what the world "
                               "told it, before acting on it",
            "ai:event:since": "Your assistant reads what it has not yet "
                              "absorbed",
        },
    },
    "ai:storage": {
        "label": "AI results",
        "actions": {
            "ai:storage:get": "Read a stored result behind a table or chart",
            "ai:storage:create": "Your engine stores a verified result",
        },
    },
    "agents:agent": {
        "label": "Agents",
        "actions": {
            "agents:agent:list": "See which agents are installed",
            # In the catalog because the BASELINE grants it (a policy
            # naming an action outside the catalog is refused at
            # validation); only a delegated runtime can actually call it.
            "agents:agent:fetch_package": (
                "Let your chat's engine fetch the code of an agent your "
                "organization approved"
            ),
            # The same shape as the pull door: the baseline grants it,
            # and only a delegated runtime can call it.
            "agents:agent:pinned_digests": (
                "Let your chat's engine find out which agent code is "
                "still approved anywhere, so it can delete the rest"
            ),
            "agents:agent:prepared": (
                "Let your chat's engine report that an agent's code is "
                "ready to run, so the Agents page can say so"
            ),
            "agents:agent:available": (
                "See the agents this deployment could install"
            ),
            "agents:agent:get": "Read an agent's manifest",
            "agents:agent:install": "Install an agent",
            "agents:agent:delete": "Remove an installed agent",
            "agents:agent:sources": "List saved agent repository sources",
            "agents:agent:sourcecreate": "Save an agent repository source",
            "agents:agent:sourceupdate": "Edit an agent repository source",
            "agents:agent:sourcerefresh": "Refresh an agent repository source",
            "agents:agent:sourcedelete": "Remove an agent repository source",
            "agents:agent:sourcepurge": (
                "Uninstall every agent installed from a source, then "
                "remove the source"
            ),
            "agents:agent:sourcetransfer": (
                "Hand one of your agent sources to another member"
            ),
            "agents:agent:source_manage_any": (
                "Edit, refresh, remove and transfer any of the "
                "organization's agent sources, not only your own"
            ),
            # An agent's sample data, loaded as the person's own records and
            # files and taken back on request. Baseline: it mints nothing
            # the person could not create by hand.
            "agents:agent:samples": "See what sample data an installed agent ships",
            "agents:agent:loadsamples": "Load an installed agent's sample data as your own records",
            "agents:agent:removesamples": "Remove the sample data you loaded",
            # Handing out access to an agent is a platform act, so it
            # IS a catalog action — the grants it writes are not.
            "agents:agent:grants": "See who may use an installed agent",
            "agents:agent:grant": "Give someone access to an installed agent",
            "agents:agent:revoke": "Take away access to an installed agent",
            # The same act, pointed the other way: which stored credential
            # an agent may read. Declaring the shape of one is the agent's
            # to do; handing over a filled-in credential is a person's.
            "agents:agent:secretgrants": (
                "See which credentials an installed agent may use"
            ),
            "agents:agent:secretgrant": (
                "Let an installed agent use one of your credentials"
            ),
            "agents:agent:secretrevoke": (
                "Take a credential away from an installed agent"
            ),
            # The same grant, repeated: one connected account to every
            # installed agent of that provider that could take it.
            "agents:agent:secretlendable": (
                "See which other installed agents a connected account "
                "could be lent to"
            ),
            "agents:agent:secretlendmany": (
                "Let several installed agents use one of your "
                "credentials at once"
            ),
        },
    },
    "ai:audit": {
        "label": "AI audit trail",
        "actions": {
            "ai:audit:list": "Read the record of what agents did for you",
            "ai:audit:list_all": (
                "Read the organization's whole audit trail — everyone's "
                "chats, installs, grants and credential reads"
            ),
            "ai:audit:record": (
                "The runtime's own write: every function it ran, its "
                "inputs in outline, its outcome and duration"
            ),
        },
    },
}


# The only gateway endpoints a delegated runtime may dispatch — the
# protocol's runtime-operations table. A delegation acts for a user but
# is not the user: IAM, account, and chat management stay browser-only
# no matter what the user's own policy allows.
#
# This is a FENCE, not a grant: passing it only means the endpoint is not
# forbidden to a delegation, and the user's own policy is still consulted
# afterwards. Patterns are matched with fnmatchcase.
RUNTIME_ENDPOINTS = (
    # The services contract (docs/system/chat-session.md): the assistant's
    # authority, its mind, its inbox, its cards, its clock.
    "ai:chat:contract",
    "ai:state:get", "ai:state:save",
    "ai:event:record", "ai:event:since",
    "ai:approval:open", "ai:approval:expire",
    # A session opening reads which questions a dead process left
    # waiting, to close them.
    "ai:approval:list",
    "ai:schedule:load", "ai:schedule:add", "ai:schedule:ran",
    "ai:schedule:remove",
    "ai:chat:plan", "ai:chat:title",
    # Events: the runtime appends its chat's sequenced narration;
    # reading (replay) is for the user.
    "ai:event:append",
    # The trail: the runtime records every function it ran — reading
    # the trail stays the person's.
    "ai:audit:record",
    "ai:message:create", "ai:message:list",
    # Storage: the runtime records results and resolves reference
    # inputs against its own chat's results.
    "ai:storage:create", "ai:storage:get",
    # A credential at use: a slot's own secret, the instances the
    # person may choose between, a site login an agent asks for.
    "secrets:secret:use", "secrets:secret:instances",
    "secrets:credential:resolve",
    # The model key, at the settings domain's own door — by connection
    # id, or the organization's default; the delegating person's
    # visibility applies either way.
    "settings:llm:use",
    # Skills: the model pulls a skill body the user could read anyway;
    # authoring stays browser-only.
    "skills:skill:list", "skills:skill:get",
    # An MCP server's address and credential, for a call to one of its
    # tools; adding and changing servers stays browser-only.
    "mcp:server:use",
    # The pull door: approved package bytes for this organization's own
    # agents, so a runtime can materialize code the scope announced.
    "agents:agent:fetch_package",
    # The reclaim read: which digests anybody still pins, so a runtime
    # can delete code and environments nobody approves any more. The one
    # entry here that answers beyond the caller's own organization, and
    # it is deliberate — a runtime's disk is shared by digest, so a
    # per-organization answer could never authorise a deletion. Hashes
    # only, to a process already holding the code they name, and the
    # controller refuses anyone but a runtime principal.
    "agents:agent:pinned_digests",
    # The readiness report: a runtime that built an agent's environment
    # says so, and the Agents page turns Preparing into Ready.
    "agents:agent:prepared",
    # Memory: the runtime reads what it was told to remember and adds
    # to it through the remember action. Forgetting is the person's
    # own act — a model that could delete its own memories could
    # quietly rewrite what it knows about someone.
    "settings:memory:list", "settings:memory:create",
    # Records: keeping them IS what an agent does for a person, so the
    # engine reads, writes, edits and deletes them. What it may not do
    # is hand them to somebody else, or re-home records it did not
    # make: a transfer is the person's own act, done from the page.
    "data:record:list", "data:record:get", "data:record:shapes",
    "data:record:create", "data:record:update", "data:record:delete",
    "files:file:upload", "files:file:download", "files:file:get",
    "files:file:list", "files:file:delete",
)
