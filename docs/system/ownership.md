# Ownership and deletion

**Every owned thing has one steward, and stewardship is transferable**
— by the steward, by an administrator holding the domain's escape
grant, and by the platform when a person leaves. Deleting a person is
a hand-over, not a disappearance. Who may *see* a thing is
[sharing](sharing.md); this page is who may *change* it, and what
happens to it when its steward goes.

## Who owns what

Every stored thing records its creator. Ownership decides who may
change or delete it; visibility (the `owner` map) decides who may see
it. Being shown something is never authority over it.

| Domain | Steward | Who may edit, share, delete, transfer | Escape grant for administrators |
|---|---|---|---|
| Secrets | `created_by` | the steward | `secrets:secret:set_owner_any` |
| Records | `created_by` | the steward | `data:record:set_owner_any` |
| Files | `created_by` | the steward | `files:file:set_owner_any` |
| Skills | `created_by` | the steward | `skills:skill:set_owner_any` |
| LLM connections | `created_by` | the steward | `settings:llm:manage_any` |
| Agent sources | `created_by_id` | the steward | `agents:agent:source_manage_any` |
| Installed agents | the organization | anyone holding `agents:agent:delete` | not needed |
| Agent grants, credential grants | the organization | anyone holding the grant action | not needed |
| Chats, messages, stored results, schedules, approvals | `user_id` | the person only: a chat cannot be shared, and opening one and sending to it are separate actions (`ai:chat:open`, `ai:chat:sendmessage`) | none |
| Memories | `user_id` | the person only | none |
| MCP servers | `created_by` | the person only; never shared or transferred | none |
| API keys | `user_id` | the person only | none, deliberately |
| Audit events | actor recorded | nobody — append-only | — |

The escape grants are held by FullAccess through its wildcard, so an
administrator is never locked out of infrastructure a colleague set up
— the two infrastructure grants reach every model connection and agent
source of the organization, an unshared one included — and a narrower
role can be given without them. The four `set_owner_any` grants reach
only documents their holder can already see: a credential or record a
colleague kept private changes hands when its owner hands it over or is
deleted, and not otherwise. In the four data-layer domains it is
checked where the domain checks its steward
(`Sharing.may_edit(..., escape=...)`,
`backend/api/services/data_layer/base.py`). Model connections, agent
sources and MCP servers compare the steward themselves, each in its
own controller, to the same rule.

## Transfer

Each domain has a transfer: `Secrets:Secret:transfer`,
`Data:Record:transfer`, `Files:File:transfer`, `Skills:Skill:transfer`,
`Settings:Llm:Transfer` and `Agents:Agent:Sourcetransfer`. It sets the
steward to another active member of the organization and replaces the
old steward with the new one in the owner map, so visibility is
preserved. Allowed to the steward and to holders of the escape grant;
audited.

## What refuses, and why

- **Deleting a secret** is refused while an agent holds a grant on it.
- **Deleting a user** is refused when it would leave nobody able to
  manage access, and when the successor is not an active member.
- **Deleting a source** is refused while agents are installed from it:
  the source holds the map from the repository's ids to the refs the
  agents were approved under. The remove dialog lists those agents and
  offers **Uninstall N and remove source**, which uninstalls each and
  then deletes the source.
- **Uninstalling an agent** is never refused. It withdraws the approval,
  deletes its grants and credential grants, prunes the credential
  definitions nothing else uses, and reclaims package bytes no other
  agent pins ([agent code](agent-code.md)). Records and files the agent
  kept stay: they are the people's.

## Deleting a chat

The schedules go first, and the runtime is told while its connection
still stands, since it holds the chat's clock in memory. Then the
connection is closed and the delegation rows go, then messages, stored
results, events and approvals, then the chat's own uploads, bytes
included, then the chat. Files the chat merely referenced
are untouched.

## A person leaving

`IAM:User:delete` takes a `successor_id` — an active member of the
organization, the deleting administrator by default — and
`IAM:User:leaving` previews what the person owns, by domain, with
counts, so the administrator sees the hand-over before confirming.

| Owned thing | What happens |
|---|---|
| sessions, password resets, API keys, invitations, runtime delegations | dropped |
| schedules | deleted with their chats — nothing keeps acting for a person who is gone |
| chats and everything under them | deleted, with the chat cascade; conversations are personal |
| memories | deleted |
| MCP servers, push subscriptions, the note of which sample data they loaded | deleted |
| secrets, records, files, skills | stewardship transferred to the successor; the owner map keeps every group and person it named; a credential granted to agents keeps working. A secret whose name the successor already uses for the same kind arrives as *name (transferred)* |
| LLM connections, agent sources | stewardship transferred to the successor |
| agent grants naming the person | the person is taken out of them; a grant left naming nobody is deleted |
| credential grants | kept; they name the secret, which now has a steward |
| audit events | kept, with the actor as recorded — a trail outlives its actors |

## Disabling a person

Disabling keeps everything the person owns. It drops their browser
sessions, password resets and API keys, deletes their delegations and
closes their live runtime sessions, and **pauses their schedules**, so nothing acts unattended for someone who may not
act. Re-enabling restores the person and leaves the schedules paused;
they resume what they still want.

## Organizations

Disabling an organization refuses everyone in it and keeps its data.
There is no organization delete in the application.

Tests: `backend/tests/test_ownership.py`.
