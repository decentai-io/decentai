# Sharing

Who may see a thing, whom it may be shared to, and who may change it.

Almost everything a person makes on this platform can be shared — a
credential, a file, a record an agent keeps, a skill, an LLM connection,
a saved agent source. Each of those could have grown its own sharing
rules, and that is the version where sharing a file and sharing a
credential drift into meaning subtly different things, and the next
shareable type added is the one that leaks. So the platform has exactly
one implementation of sharing, and this document describes it.

The implementation lives in `server/governance/sharing.py`. It sits
beside `server/authentication` on purpose: authorization answers *"may
this caller perform this action"*, governance answers *"how far does
what they make reach"*. Both the API layer and the database layer stand
on it — controllers clean and refuse with it, stores filter every read
through it — which is why it lives above both rather than inside either.

---

## The three questions

Every shareable document answers the same three questions the same way.

**Who may see it.** Each document carries an `owner` map — a set of
group ids and a set of user ids. A reader sees the document when they
are named in `owner.users`, sit in one of `owner.groups`, or the map
names the Everyone group, which is the organization-wide share. This is
not advisory: every read the stores serve is filtered through it, pinned
beside the reader's organization, so a document outside a caller's
reach does not exist as far as the API can tell.

**Whom may it be shared to.** A writer's reach is their own position:
the groups they explicitly belong to, and the people who share one of
those groups with them — their *peers*, the same boundary the "share
with a person" picker draws, so the picker and the refusal always
agree. Nobody shares into a group they do not sit in, and nobody names
a stranger, without the domain's escape grant (below). Ids that do not
exist in the caller's organization are refused loudly rather than
stored inertly: a share that silently reaches nobody is a typo, and
typos get refused.

**Who may change it.** The creator, recorded in `created_by` — its
*steward*. Seeing a shared document is never authority over it — a
colleague you shared a credential with can use it, not edit or delete
it. A row with no creator recorded is editable by nobody but the
holder of the domain's manage-any grant.

Stewardship is transferable. Each domain has a `transfer` action —
`secrets:secret:transfer`, `data:record:transfer`, `files:file:transfer`,
`skills:skill:transfer`, and the same for model connections and agent
sources — that hands a document to another active member of the
organization: `created_by` changes, the old steward's place in the owner
map is taken by the new one, and every group and person it named keeps
seeing it. The steward may transfer, so may a holder of the domain's
escape grant, and the platform does it for everything a person owns when
they are deleted (see *Identity and Access*). A transfer to oneself is
refused; it is not a transfer.

---

## Profiles: the policy knobs

What differs between record types is policy, not mechanics, and the
differences are deliberate. A `SharingProfile` carries the knobs; the
`Sharing` engine is constructed with one:

```python
Sharing(PERSONAL, "secret")          # a data-layer domain
Sharing(INFRASTRUCTURE, "source")    # an org-infrastructure type
```

**Everything defaults to private.** A create that names no owner is
visible to its creator alone — LLM connections and agent sources
included. Nothing a person makes reaches anyone else until they say so.

**PERSONAL** — secrets, files, agent records, skills. Sharing
organization-wide (naming Everyone) is not open to everybody; it is
part of what the escape grant lifts.

**INFRASTRUCTURE** — LLM connections, agent sources. These exist to be
used, so naming Everyone is open to whoever creates one. The creator is
always retained among the users — an infrastructure record is never
shared out of its author's sight.

Both profiles allow person-shares to peers. A future shareable type is
one constructor call with whichever profile fits, or one new profile
when its policy genuinely differs.

Presentation stays at the edges. The engine returns *what* is out of
reach; each consumer words its own refusal, so the sentences the tests
pin cannot be changed by accident from underneath every page at once.
The one wording two pages already share — "You can only share to groups
you belong to…" — ships on the engine as `reach_refusal`, for consumers
that take it as-is.

## The escape grant

Each domain names one action — `secrets:secret:set_owner_any`,
`files:file:set_owner_any`, and so on — that lifts the boundary and the
creator-only rule together. A holder may share organization-wide, into
any group, to any person, and maintain documents created by others. It
is one grant per domain on purpose: the administrator who may re-home
every credential is not automatically the administrator who may re-home
every file.

Infrastructure has its own pair. Agent sources and model connections are
not shared documents — a source is either private or organization-wide,
a connection is visible to everyone — but they have a steward all the
same, and `agents:agent:source_manage_any` and `settings:llm:manage_any`
let a holder edit, remove and transfer any of them. Both sit
under the wildcard, so an organization's administrators hold them
without a change, and a narrower role can be given the marketplace
without them.

## What sharing is not

One neighbouring mechanism is deliberately *not* this one.

**An agent's use of a credential** is a grant, not a share. Sharing a
secret with a colleague lets the *person* see it; whether an *agent*
may read it is a separate, revocable decision recorded per agent and
slot (`agent_secret_grants`). The two compose: a peer you share a
credential with can grant it to agents they manage, because granting
rides on visibility.

A connected account carries two facts a typed-in credential does not:
the provider that signed it and the scopes the person consented to. A
grant checks both — a Google account is not offered to a Microsoft
slot, and an account consented for mail alone is refused by an agent
that declared files too. Because one sign-in lands in one agent's slot
and every other agent of that provider needs its own grant, the moment
after a Connect offers to lend the new account to all of them at once
(`agents:agent:secretlendable`, `agents:agent:secretlendmany`). That
writes the same rows and the same audit events as lending one at a
time; it is a shortcut, not a new kind of access.

---

## What the engine serves

For the writing side, constructed with a profile and the label its
refusals name:

| Method | Answers |
| --- | --- |
| `clean(owner, creator=)` | The normalized owner map, or a ValueError saying what is wrong |
| `check_exists(org_id, owner)` | Refuses ids that are not this organization's |
| `beyond_reach(user, owner)` | What this writer may not share to, as labels |
| `reach_refusal(user, owner)` | The shared 403 wording, for consumers that take it as-is |

For reading and changing — policy-free, so static:

| Method | Answers |
| --- | --- |
| `visibility_filter(user)` | The whole enforced read filter: org pin, then owner |
| `visibility_or(user)` | The owner half alone, for stores composing larger filters |
| `org_wide(owner)` | Whether the map names Everyone |
| `may_edit(user, doc)` | The creator-only rule, escape included |

The organization never appears *inside* an owner map — reads pin it
beside the owner, and writing it into the map would be the org-wide
share under a name the filter does not read. An owner naming nobody is
refused for the same reason from the other side: the document would be
invisible to everyone, its creator included.
