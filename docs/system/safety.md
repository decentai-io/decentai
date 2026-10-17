# Safety settings

Two agents do things nobody can list beforehand: the Browser opens any
site and runs scripts in its pages, and the Code agent writes programs.
Both put each script or program to the person on a code card
([chat session](chat-session.md)) after the assistant has read it and
said whether it does what it says (`ai_runtime/chat/code_review.py`).
For a person who uses them every day that can be a card too many, so
how often to be asked is theirs to say. And every connection any agent
makes passes the platform's proxy ([the sandbox](sandbox.md)), which
keeps a record of where each one went.

## What a person sets

One page, **Settings → Safety**, which the administrator sets for
everybody (`Settings:Safety`, kept on the organization). Seven rows,
each starting where the platform stood before the setting existed: the
Browser's scripts and the Code agent's programs ask every time, any
package may be installed, MCP servers and webhooks are allowed, and
notifications say what happened:

| Row | The choices | The start |
|---|---|---|
| **Sites no agent may open** | a list of names; `example.com` covers every host under it | empty |
| **A script in a page** | *Ask every time* · *Ask once for a site in a chat* | every time |
| **A program** | *Ask every time* · *Do not ask again for a correction* · *Ask only when it reaches a site or uses a credential* | every time |
| **Packages a program may install** | *Any package the card names* · *Only packages on my list* | any |
| **MCP servers** | *People may add* · *Nobody may* | allowed |
| **Receiving webhooks** | *People may receive webhooks* · *Nobody may* | allowed |
| **What a notification says** | *Say what happened* · *Only that something did* | says what happened |

Three rules hold under every choice:

1. **The assistant's reading decides whether a choice applies.** Code
   is let through without a card only when the review says it does what
   it says. Code that does more, or that could not be read, is put to
   the person whatever they chose.
2. **What is let through is still said.** Code that ran without a card
   leaves a message in the chat, in the agent's name: what it was for,
   what the review made of it, and the code (its first twenty-five
   lines). Its card is on the record, answered by the setting, and the
   audit trail says so. In a sub-assistant's work the chat is shown one
   line, *Ran without asking…* with what it was for; the whole is on
   the card's record.
3. **Only a person's own yes counts as having allowed.** "Once for a
   site" and "a correction" both rest on something a person allowed
   before. A card the setting settled is not that: a setting never
   vouches for itself.

What each looser choice means, exactly:

| Choice | Let through when |
|---|---|
| A script, once for a site in a chat | a person allowed a script of the same agent on the same site in this chat |
| A program, not again for a correction | the same call already had a program a person allowed, and this one names no package, host, credential or file they did not allow then |
| A program, only when it reaches a site or uses a credential | it names no host and no credential; or it is a correction, as above |

## Where each is enforced

| Row | Where | How |
|---|---|---|
| Sites no agent may open | the proxy (`ai_runtime/agents/egress.py`) | a name on the list is refused to every worker, a manifest that says `any` included, and the refusal says the site is on the list. The list is of names, so while it holds any, no worker reaches anything by a bare address — an address says nothing of whose site it is. (An MCP server is called by the runtime and not through the proxy: its name is held to the list in the executor, and an address is not refused there.) The list is told to the worker's pass as each call begins, so a change applies from the next call |
| A script, a program | the backend, as the card is opened (`backend/api/services/chat_session/code_rules.py`) | the runtime proposes and has the code read; `AI:Approval:Open` then answers the card itself where the rule allows, and says so (`settled`). The runtime opens cards and never decides them, so it cannot loosen itself |
| MCP servers | the backend (`backend/api/services/data_layer/mcp.py`) | blocked, nobody adds or reads a server, the chat's contract names none, and the runtime's door to a server's address is refused ([MCP servers](mcp.md)) |
| Receiving webhooks | the backend, at the door (`backend/server/routes/hooks.py`) | blocked, every inbound webhook of the organization answers 404, as one that does not exist would; none is deleted, and no new one can be made (403). Notifications going out are not affected ([webhooks](webhooks.md)) |
| What a notification says | the backend, as a notification is delivered (`backend/server/notifications.py`, `Notifier.quiet`) | hidden, every push — to a phone through Expo, Apple or Google, to a browser through its push service — and every email says only *Something is waiting for your decision* or *A chat of yours has an answer*, with the link to the chat; no chat title, agent, function or words of a reply. A person may choose the same for their own (`Settings:Notifications:Update`, `text`), and cannot undo the organization's. Outbound subscriptions are the person's own addresses and are not affected ([webhooks](webhooks.md#outbound)) |
| Packages | the runtime, where a proposal is checked before its card (`ai_runtime/execution/call.py`, `code_problem`) | a package that is not on the list is refused before anybody is asked, and the agent is told which names are. A listed package's own dependencies are installed with it |

None of it is the agent's to decide: an agent proposes, and hears yes
or no. The chat's contract carries to the runtime only what the
runtime enforces — the blocked names and the package list. How often a
card is shown is not in it.

## The organization's own rules

Below the rows, the same page holds text the organization writes for
its assistant: **Your organization's rules for the assistant**
(`Settings:Guardrail`, kept on the organization as `guardrail: {text,
updated_by, updated_at}`). It is what this organization wants the
assistant to do or avoid that no row says: *never write to the Finance
notebook*, *answer customers in the language they wrote in*, *check
the budget sheet before ordering anything*.

| | |
|---|---|
| **The text** | at most 4,000 characters (`OrganizationStore.GUARDRAIL_MAX`), kept trimmed, line ends made one kind. Newlines and tabs are allowed; any other control character is refused, since it is invisible on the page and only a surprise in a prompt. Empty is none |
| **Who may read it** | whoever holds `settings:guardrail:get` |
| **Who may change it** | whoever holds `settings:guardrail:update` |
| **Where it goes** | into the assistant's frame, in a block of its own, `ORGANIZATION'S OWN RULES`, after every instruction the platform gives and before the plan, the summary, the agents, the skills and the memories ([the assistant](assistant.md#the-frame)); a sub-assistant's frame carries the same. And into the code reviewer's instructions: code that would break a rule is said to, and never *agrees* |
| **When a change applies** | at the next turn of every chat, an open one included: the runtime reads it with the contract as each turn begins ([chat session](chat-session.md)) |

Neither action is in the baseline or a member's: an administrator holds
them through `*` and gives them to whom they choose, in a policy like
any other ([identity](identity.md)) — reading to a few, changing to
fewer. A person who holds only `settings:guardrail:get` finds the page
with that section alone; one who holds only Safety's actions sees the
rows without it.

The rules reach the runtime in the contract it reads for a chat
(`AI:Chat:Contract`, `guardrail`), and not in the one the page is
served at `AI:Chat:Open`: a person who may chat need not hold the grant
to read them. They are still words the model is given, and a person in
a chat may get the assistant to repeat them: they are no place for a
secret.

**It is guidance, not enforcement.** The block tells the model the
rules can narrow what it does but never widen it: they cannot unlock an
action that needs approval or turn off a safety gate, and where one
seems to ask for that, the instructions above them win. But nothing in
code reads the text. What must never happen belongs in the rows above,
in people's grants and in the chat's trust level, which hold whatever
any prompt says; the rules make the assistant better at what it is
already allowed to do.

The model is told them; agents are not. An agent's own `call.llm`
speaks to a model with the agent's prompt, for the agent's purpose,
and the organization's rules for its assistant are not added there:
what an agent may do is its manifest and its grants, and an agent's
prompt is its author's. Nor are they in the prompts that title a chat,
fold it into a summary or rerank agents: those choose words, not what
is done.

A change is on the trail as `settings.guardrail`, with the length of
the text before and after and the first twelve hex digits of each
one's SHA-256 — not the text. More people read the trail than may
read the rules: the length says how much changed and the fingerprint
tells two versions apart, or matches one someone holds, without
repeating what either says. Saving the same text again writes nothing
and leaves who last wrote it as it was.

## Stopping

Two stops are the person's own, and neither waits for anything to
agree to end.

**One chat.** A chat has two stops. The one beside the message box,
*Stop generating*, asks the assistant to stop where it next can. The
one in the chat's header, *Stop everything in this chat*, once
confirmed, is its kill
([session door](../reference/session-door.md), `stop` with `force`):
every job is cancelled where it stands, a worker that does not end is
ended, the chat's helpers and its browser go, its open cards expire and
the fires its clock had under way are cut. What was done stays done;
the next message starts a quiet chat that knows what it did.

**Everything.** The stop at the top of every page ends all of a
person's chats at once and holds them stopped (`AI:Switch`,
`backend/api/services/chat_session/halt.py`). In order:

1. The person is marked stopped. From that moment nothing of theirs is
   dialed and no key is minted for any chat of theirs — a message, a
   decision on a card, a schedule coming round, a boot's re-dial, a
   webhook's event. Their webhooks answer 404 to every call until they
   resume ([webhooks](webhooks.md)).
2. Every chat a runtime may be serving for them is killed as above:
   the ones they have open, the ones working with nobody watching, the
   ones that only keep a clock. Each is given ten seconds to write
   down what it had done.
3. Every key the runtime holds for them is withdrawn. Whatever is
   still running then — a process that ignored its end — can read no
   credential, no model key, no record and no file. It starves.

It holds until the person resumes, which is their own act and never
happens by itself; the chats that keep a clock are dialed back then,
and the others wait to be opened. Both are on the audit trail
(`work.stopped`, `work.resumed`). The switch reaches the person's own
chats and nobody else's, and the runtime's delegation has no hand on
it: what is stopped cannot start itself again.

## The record

The proxy counts, for each worker, the hosts it connected to. When a
call ends, what its worker reached while it ran is written on the
call's line in the audit trail: the names, the busiest first, and how
many connections to each. A browser loads a page from dozens of hosts,
so a line carries the first twenty and the count of the rest. A worker
serving two calls at once counts for both.

Nothing is kept about what was sent: the proxy never sees it. A change
to the Safety setting is on the trail too, with what each row was and
what it became.

## What it does not do

- **It does not read what an agent sends.** A site is a name and a
  port; reading the contents would mean opening encrypted traffic.
- **It does not limit the Browser to a list of sites.** A page is
  loaded from many hosts — its own, and the ones its pictures, scripts
  and sign-in come from — and a list of allowed names at the proxy
  would break the page.
- **It does not change what an approved manifest declared.** An agent
  that declared its hosts reaches those, less the blocked ones.
- **It is one setting for the organization**, not one per person.
- **The record has no page of its own.** It is read in the audit trail.
- **Where an agent's connections are not fenced to the proxy**, they
  can go around it: the blocked names are not enforced there and
  nothing is counted. The agent's page says when that is so.
