# Identity and Access

Who exists in DecentAI, what is recorded about them, and how the platform
decides what any of them may do.

This document covers identity and authorization only. The AI runtime acts
inside the platform under a delegated identity of its own; that delegation
is named at the end of this document and described in full elsewhere.

## The shape of the model

An identity here is never a set of permissions. It is a row, and a chain
that leads from that row to the things it may do:

```
user → groups → roles → policies → statements
```

The chain is walked on every request. Nothing about authority is copied
into a token, a session, or a cached credential, because anything copied
is something that can be revoked in one place and still honoured in
another. A group changed a moment ago governs the next request, not the
next login.

Two rules follow from this and hold everywhere in the document:

**An action is an endpoint.** Every operation the platform exposes is
named by the operation itself — `iam:user:set_groups`, `secrets:secret:create` —
and that name is what a policy grants. There is no separate permission
vocabulary to keep in step with the API, and no endpoint that decides for
itself whether the caller is allowed. One check, in one place, using the
name of the thing being called.

**Nothing is permitted by default.** A request is refused unless some
statement reachable from the caller allows it. Absence of a rule is a
refusal, not an opening.

---

## Organization

An organization is the top-level identity and the isolation boundary. One
deployment hosts many, and they cannot see each other.

| Field | Meaning |
| --- | --- |
| `org_id` | Unique, immutable identifier |
| `org_name` | Human-readable name; descriptive only |
| `status` | `active` or `disabled` |
| `created_at` | When it was stood up |

Every user, group, role, policy, invitation, session and stored resource
belongs to exactly one organization, and every query is independently
scoped by it. Isolation is a property of each lookup rather than a filter
applied at the edge, so a missing check cannot widen a query into another
tenant's data.

`org_id` is the identifier authorization uses. `org_name` may change and
means nothing to any decision.

An organization is not merely a row: it is a row plus the access chain
that makes it usable. Standing one up seeds both, so a deployment's first
organization and its tenth are laid out identically:

- `FullAccess` → `Administrator` → `Administrators`
- `BaseAccess` → `User` → `Everyone`
- `MemberAccess` → `Member` → `Members`

**Members** is what a person who is not an administrator is put in to
use the platform: their own chats and what the assistant does in them
on their behalf, their files, saved data and credentials, and seeing
the agents they were given (`MEMBER_ACTIONS`, beside the catalog).
Installing agents, the organization's settings and other people are not
in it. Without it, or a group like it, a person signs in and can do
almost nothing: chatting is not among the actions everybody holds. The
Users page offers it already ticked for a new person. It is seeded once
and is an ordinary group from then on — an administrator edits what it
grants, or removes it, and it is not put back. Which agents its people
may call is a separate decision, made on each agent: a newly installed
agent reaches whoever installed it and their groups, and on a person's
own computer Members as well, where the people are few and were added
by the one installing. Either grant is withdrawn on the agent's page.

Creating an organization, and disabling or re-enabling one, happen from
outside the application entirely — they are deployment operations, not
features of the product, and no administrator inside an organization can
perform them. A disabled organization keeps all of its data and refuses
everyone in it: its members' sessions are dropped, and any that survives
the moment of the decision is refused on its next request.

## User

A person, belonging to one organization.

| Field | Meaning |
| --- | --- |
| `user_id` | Unique, immutable identifier |
| `org_id` | The organization they belong to; immutable |
| `email` | Login identity, unique across the whole deployment |
| `user_name` | Display name |
| `status` | `active` or `disabled` |
| `assigned_groups` | Group membership — the whole of their access |
| `created_at`, `last_login_at` | Lifecycle timestamps |

A password hash is held but is never part of any view of a user, and no
API returns it under any permission.

**Email is the login identity deployment-wide.** One address belongs to
one person in one organization; it cannot be invited into a second. This
is a deliberate simplification: an address that could mean two people
would make every "who is this" question ambiguous at exactly the moment
it matters.

**Group membership is the entirety of a user's access.** There is no
permission attached to a user directly, no ownership tier, and no
administrator flag. Someone is an administrator because they are in a
group whose roles carry a policy that allows administrative actions —
which means the answer to "why can this person do that" is always a path
that can be followed and changed.

Authorization uses `user_id` and live group membership. It never uses
`email` or `user_name`, both of which may change and neither of which
means anything to a decision.

A disabled user cannot authenticate and cannot continue: their sessions,
API keys and any delegations made on their behalf are dropped, their
live chats are closed, and every schedule they own is paused. Nothing
acts unattended for someone who may not act. Re-enabling restores the
person, not the clock: the schedules stay paused until they resume the
ones they still want.

**Deleting a user is a hand-over, not a disappearance.** Everything a
person made has one steward, and a steward who leaves is replaced by a
*successor* — an active member of the same organization, the deleting
administrator unless another is named. `IAM:User:Leaving` previews what
the person owns, by kind, with counts; `IAM:User:Delete` then, in one
pass:

| Owned thing | What happens |
| --- | --- |
| sessions, resets, API keys, invitations, runtime delegations | dropped |
| chats, and everything under them: messages, approvals, uploads, schedules | deleted; conversations are personal |
| memories | deleted; a profile of a person nobody can read is a liability |
| secrets, records, files, skills | stewardship passes to the successor; whoever they were shared with keeps seeing them; a credential granted to agents keeps working |
| model connections, agent sources | stewardship passes to the successor |
| agent grants naming the person | removed |
| audit events | kept, the actor as recorded |

The refusals stay: an organization is never left with nobody able to
manage access, and the successor must be an active member who is not the
person leaving. The hand-over itself is audited, with the counts.

## Group

The unit of membership, and the only thing a user is attached to.

| Field | Meaning |
| --- | --- |
| `group_id` | Unique identifier |
| `org_id` | Owning organization |
| `group_name` | Human-readable name, unique within the organization |
| `assigned_roles` | The roles this group carries |
| `is_builtin` | Whether the platform maintains it |
| `created_at` | When it was created |

Every organization has a built-in **Everyone** group whose membership is
implicit: it is resolved for every user of that organization rather than
recorded on each one. There is no membership list to maintain, so nobody
can be missing from it by accident, and a new user is covered by whatever
Everyone carries from their first request. It cannot be renamed or
deleted, and it is per-organization — one organization's Everyone is not
another's.

## Role

A named bundle of policies. Roles exist so that policies can be composed
once and attached many times.

| Field | Meaning |
| --- | --- |
| `role_id` | Unique identifier |
| `role_name` | Human-readable name, unique within the organization |
| `description` | What the role is for |
| `assigned_policies` | The policies it carries |
| `created_at` | When it was created |

A role grants nothing by itself. It reaches a person only through a group.

## Policy

Where permission is actually written.

| Field | Meaning |
| --- | --- |
| `policy_id` | Unique identifier |
| `name` | Human-readable name, unique within the organization |
| `description` | What the policy is for |
| `permissions` | A list of statements |
| `created_at` | When it was created |

A statement names an effect and the actions it applies to:

```json
{
  "effect": "Allow",
  "actions": ["iam:user:list", "iam:group:*"],
  "resources": ["*"]
}
```

- `effect` is `Allow` or `Deny`.
- `actions` are action names, or wildcard patterns over them.
- `resources` must be `["*"]`. Narrowing permission to particular records
  is not expressed here — visibility of individual documents is decided by
  each document's own sharing, not by policy.

A policy may hold at most 50 statements, and a statement at most 100
actions. A document large enough to be unreadable is refused at the door
rather than evaluated.

**Every action is validated when the policy is saved.** An action that is
not in the platform's vocabulary is rejected outright, and a wildcard must
have a literal prefix that matches something real. A typo becomes an error
at the moment it is written, rather than a permission that silently never
applies.

---

## How a decision is made

The platform holds one catalog of every grantable action, grouped by the
area it belongs to. The catalog is the whole vocabulary: an operation
absent from it cannot be named in any policy, and adding an operation to
it is what makes that operation grantable at all. The same catalog drives
the policy editor, so what an administrator can tick is exactly what the
system can evaluate.

For each request, the platform gathers every statement reachable from the
caller — their groups (including the implicit Everyone), those groups'
roles, those roles' policies — and evaluates them together:

1. Statements that do not match the action are ignored.
2. A matching `Deny` refuses, immediately and finally.
3. Otherwise a matching `Allow` permits.
4. With no match at all, the answer is refusal.

An explicit `Deny` beats every `Allow`, no matter where in the chain each
came from. This makes a prohibition something that can be stated once and
relied upon, instead of a property of how the pieces happen to be ordered.

Resolution is cached in memory for speed, but the cache is not a second
source of truth: every change that could affect a decision — group
membership, a role's policies, a policy's statements, a user disabled,
an organization disabled — discards what it invalidates at the moment it
happens. Revocation therefore takes effect on the next request rather
than at the next login or the next cache expiry.

### The baseline

Some things are not privileges. Every user may read and correct their own
profile, read and delete what the assistant has been told to remember
about them, read the organization's written knowledge, and see the shape
of a credential form well enough to fill it in. These are granted by a
baseline policy attached to Everyone.

The baseline is versioned. When the platform gains a new baseline action,
it is added to existing deployments exactly once — because an
administrator is entitled to remove a baseline action deliberately, and a
seed that re-asserted the whole list on every restart would quietly undo
that decision.

---

## The two invariants

These are the properties the model is built to guarantee, rather than
conventions it happens to follow.

### An organization cannot lock itself out

Access is stored in a chain, and a chain can be cut. Someone could remove
the last group carrying administrative access, or delete the policy that
grants it, and leave an organization that no one inside it can repair.

So every change to the access chain asks one question before it commits:
does at least one **active user of this organization** still resolve the
ability to place users into groups? That capability is the recovery
capability — whoever holds it can restore anything else. If the answer is
no, the change undoes itself and is refused.

The check is per organization. Another organization's administrator is no
comfort to the one that just locked itself out.

### Nobody can grant what they do not hold

Otherwise anybody who could write a policy could write themselves a
better one.

An author may only allow actions they themselves already hold. This is
enforced not just when a policy is written, but along every edge of the
chain: attaching a policy to a role, a role to a group, a group to a user.
Escalation cannot be laundered through an attachment, because the
attachment is checked the same way the authorship is.

Only `Allow` is bounded this way. A `Deny` can only ever narrow access, so
anyone may write one.

---

## Becoming a user, and proving it later

Nobody signs themselves up. A person exists in an organization because
somebody who was already there invited them — or, on a person's own
computer, added them.

**Invitation.** An invitation records the address invited, the
organization, any groups the person should land in, who invited them, and
when it expires — seven days by default. Accepting it creates the user and
signs them in. The address on the new account comes from the invitation
and never from the request that accepts it: the link proves control of one
mailbox, and that is the only address it can produce. The organization
comes from the invitation for the same reason.

**A person added by hand.** A desktop sends no email, so there an
administrator adds a person directly (`IAM:User:create`, refused on a web
deployment) and is shown a temporary password once, to hand over. The same
is done for a person who forgot theirs (`IAM:User:reset_password`, on any
deployment), which also ends their sessions. It is bounded as group
assignment is: nobody resets the password of a person holding more than
they do, since that would be taking their account. A handed-over password
proves who the person is and opens nothing: signing in with it is answered
`change_required` and no session, and the person chooses their own
(`POST /auth/password/first`, which asks for the handed-over one again)
before their first session exists. An administrator's own forgotten
password is reset on the computer itself (`bootstrap/reset_password.py`).

**Password.** Passwords are stored as a slow one-way hash, deliberately
expensive to compute, over a fixed-length digest of the password — so that
a long passphrase is never silently truncated by the hashing algorithm's
input limit. A password must be at least 10 characters and contain a
letter and a digit. Composition rules beyond that are absent on purpose;
length does more for safety than required punctuation.

**Session.** Signing in creates a session row and hands the browser a
signed cookie that *names* it. The cookie is a pointer, not a container:
its claims are read to find the session, and never to decide anything.
Every request re-reads the session and the user behind it, which is what
makes disabling, deleting or signing out take effect immediately rather
than whenever the cookie would have expired. Sessions last seven days, or ninety
when the person asked to be kept signed in on that device, and
record the browser and address they were opened from. A request carrying
the cookie from a page of another origin — another site, or another
program on another port of this computer, which browsers send the cookie
to as well — is not signed in: the browser names that page in `Origin`,
and only the app's own addresses (`PUBLIC_APP_URL`, `CORS_ALLOW_ORIGINS`)
are accepted.

**Forgotten passwords.** A reset link lives one hour and works once.
Only a fingerprint of it is stored, so nobody who reads the database can
replay it as the link. Completing a reset ends every session that user
had and revokes their API keys, on the principle that the usual reason to
reset a password is that somebody else may have had it — and a key is
how they would have kept access. Requesting a reset answers identically
whether or not the address has an account, in the same time — the mail
leaves on a thread of its own — and the link itself travels by email and
nowhere else. Where no mail server is set, no link is made at all: the
answer says how this install resets a password instead
(`bootstrap/reset_password.py` on a desktop, whoever runs it on a server).

**Failed attempts.** Sign-in attempts are counted in a fifteen-minute
window: a handful of wrong passwords locks that account briefly, and a
higher count from one network address catches somebody working through
many accounts. A successful sign-in clears the account's count but not the
address's, because succeeding against one account must not refill an
attacker's budget against the rest. The throttle is consulted before the
password is checked, so a locked window answers the same for a right
password as for a wrong one.

Each of these — invitations, sessions, resets, throttling — is scoped and
swept the same way as everything else: they belong to an organization,
they die with the user, and they are re-read rather than trusted.

---

## Principals, and where this document ends

The platform recognises two kinds of caller, and one of them has two
ways in.

A **user** is a person acting through their browser, with the access this
document describes — or a script acting with one of their **API keys**.
A key is the person: the same user, organization and policy chain,
walked on every request; it grants nothing its owner lacks and loses
whatever they lose. It is shown once, at creation, and only its hash is
kept; it is presented as `Authorization: Bearer dk_…`; revoking it is
the only edit, and disabling the user revokes them all. Keys are made
and revoked from a browser session only — a key cannot mint or revoke
keys, so a leaked one cannot make itself permanent or lock its owner
out. Each person may hold twenty.

A **runtime delegation** is the AI runtime acting *for* a person, within
one conversation. It is the same identity — the same user, the same
organization, the same policy chain — with two additional constraints. It
is confined to a fixed set of operations decided by the platform rather
than by the user's own permissions, checked before their policy is
consulted at all; so a delegation cannot reach identity management,
account settings or chat administration even when acting for an
administrator who could. And it is minted for one conversation: a call
naming another chat is refused, even one the same person owns. What it
may ask agents to do is the chat's contract, read fresh each turn.
Signing out does not end it — a scheduled chat runs while its person is
away — but disabling or deleting the person does.

The consequence worth stating here is the one that belongs to identity: a
delegation never expands authority. It can only ever be the user, or less
than the user. Everything else about how a delegation is created, scoped,
carried and revoked — and what the runtime does with it — belongs to the
runtime's own documentation.
