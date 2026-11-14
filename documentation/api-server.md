# API server

## 1. What it is

The API server keeps the platform's data and decides who may do what with it. People reach it through the web
client or an API key, runtimes through a door of their own, and outside systems through webhooks. The rest of the
platform asks it for everything it keeps.

**It promises:**

- Every call comes from someone it knows, and is checked before anything is read or written.
- Nothing of one organization reaches another.
- A person reaches only what is theirs or shared with them, and does only what their organization allows.
- An agent reaches only its own records: those whose `agents` field lists it ([agents](agents.md#7-resources)), and
  only within a call a runtime makes for a chat that uses it. The API server checks this itself, and never takes a
  runtime's word for it.
- A secret's value never goes back to a person. It goes only to an agent the record lists.
- Every change and every refusal is on the audit trail.

**It never** runs an agent's code, holds a process, or speaks to a sandbox: runtimes do. It never calls a model on a
chat's behalf.

**It keeps** organizations and their people, the agents they installed, their records (secrets, data, files), their
chats, their settings, and the audit trail.

**Several API servers may serve one deployment,** sharing one database. Any of them answers any call. A chat's live
events reach its person whichever server their browser and the runtime reached, and work that must be done once is
done once. While none runs, nobody can sign in and runtimes wait and retry; nothing is lost, since everything is kept
in the database.

## 2. Who calls it

It has two doors:

| Door | Port | Who | Known by |
|---|---|---|---|
| people's | 8000, plain HTTP behind the deployment's proxy, which holds the TLS | people, their scripts, outside systems | a session token or an API key; a webhook's secret |
| runtimes' | 8004, HTTPS with mutual TLS | runtimes | their certificate |

**A person** signs in with their email and password, and gets a session token: a random value the API server keeps
only as its hash.

- Every call carries it in `Authorization: Bearer`. On the live socket it goes as the subprotocol after `bearer`,
  since a browser can't set a header there.
- Each call reads the session, the person and their organization again, in one read, so a person disabled, removed
  or signed out is refused on their next call. On the live socket too, at every input.
- What the person's groups and policies allow is kept by each server, by the organization's access version: every
  change to people's groups, groups, policies or who may use an agent raises it, and each call reads it, so a change
  holds from the very next call on every server.
- A session lasts 7 days, or 90 when they chose to stay signed in. Changing their password ends their other
  sessions; resetting it ends them all.
- There are no cookies: no other site can act through a person's browser.
- Sign-in tries are counted per account and address together, 5 in 15 minutes, and per address, 20, so nobody
  else's tries can shut a person out. Signing in clears the first count.

**An API key** is for a person's own scripts. It acts as that person, with their rights. It begins `dk_`, is shown
once, kept only as its hash, and ends when it expires or is revoked. Its lifetime is chosen when it is made: 1 to 365
days, 90 unless chosen. A person keeps at most 20.

**A runtime** is known by its certificate, from the deployment's issuer, of kind `runtime`. An administrator can
refuse one by its id, and it is then refused on every call. A certificate that names an organization serves that
organization alone; one that names none is shared, and serves every organization that doesn't keep to its own
runtimes.

- **For a chat it holds,** it acts as that chat's person, and never with more than they may. For an agent's call it
  names the agent: the API server checks that the chat uses that agent, and gives only records that list it.
- **As itself,** for no chat, it reports its own state and fetches the programs it runs: only those installed in an
  organization it may serve.

A runtime that lied could reach no more than the chats it holds and the agents those chats use.

**An outside system** is known by its webhook's secret ([8](#8-settings)).

**A provider's redirect,** when a person connects an account, is known by the state it carries: spent once, within
10 minutes, and bound to the person and the session that started it.

**The operator** has no door. They act through the deployment's settings and its admin tool, inside the container.

## 3. Calls, answers and permissions

**A call** has one shape on both doors: `{"endpoint": "Domain:Name:action", "data": {…}}`. On the people's door it
is `POST /app`, in JSON; on the runtimes' door, the communication module's call. Signing in, replacing a password
handed over, first run, signing up, resetting a password and accepting an invitation are endpoints too, with those
that look at a link before it is used: the only ones taken without a token. An endpoint nobody declared is `404`.

Other routes exist only where a call can't take this shape:

| Route | For |
|---|---|
| `POST /upload?name=…` | a file's bytes, as the body; `agent` and `resource` add it to an agent's resource (§6) |
| `GET /download/<id>` | a file's bytes, always as an attachment |
| `POST /speech` | a recording, written down (§8) |
| `/chats/<id>` | the live chat socket (§7) |
| `POST /hooks/<id>` | a webhook (§8) |
| `GET /oauth/callback` | a provider's redirect, sent on to the web client (§6) |
| `GET /healthz`, `/readyz`, `/metrics` | the probes (§11) and the metrics (§9) |

The first three are rights of their own (below), counted with the person's calls.

**An answer** is `200` with its result. A refusal is its status and `{"error": {"code", "message"}}`: the message is
words for a person, and a refusal may add a field, such as `retry_after`.

| Status | `code` | Means |
|---|---|---|
| 400 | `invalid` | the call can't be carried out as sent; the message names the field |
| 401 | `signed_out` | no valid session, key, certificate or webhook secret |
| 403 | `refused` | not allowed |
| 404 | `not_found` | nothing by that id that the caller may see, whether or not it exists |
| 409 | `conflict` | the state doesn't allow it now; the message says why |
| 413 | `too_large` | larger than the endpoint takes |
| 429 | `too_many` | too many calls; `retry_after` says when to try again |
| 500 | `failed` | the server failed; the message says nothing of how it works inside |

**Every endpoint declares three things,** and nothing else:

- who may call it: a person; a runtime for a chat it holds; a runtime writing down what such a chat did, which
  passes even while the chat's person has stopped everything (§7); a runtime as itself; or anyone, before signing
  in;
- whether it changes anything;
- what the policy editor says of it.

**Permissions** come from a person's groups, Everyone included. A group holds policies, and a policy is a list of
statements, under a name of its own in the organization that an administrator may change. Who may use an installed
agent is kept with the agent, by group (§5), and its rows are a person's statements too:

```
{"effect": "allow" | "deny" | "ask", "actions": ["iam:user:*", …], "scopes": {…}}
```

- **An action** is an endpoint (`iam:user:set_groups`), a right that is no endpoint (`files:file:upload`,
  `files:file:download` and `settings:speech:transcribe` for the routes above, and `records:manage_any`), or what an
  agent does: one of its functions, or a runtime function it calls on one of its resources.
- **A policy** holds 1 to 50 statements. Each names actions that exist, or patterns of them, or agents' actions.
- **A deny wins,** then an ask, then an allow. Nothing matching is a deny. A deny in an agent's rows or in a policy
  wins over an allow in the other.
- **`ask` and `scopes` are for agents' actions only.** An ask puts the question to the chat's person, who may
  remember the answer. Scopes narrow a function to the values its manifest's authorization scopes allow
  ([agents](agents.md#5-the-manifest)): the runtime judges a call to a function, and what its program calls within
  it, by the values its inputs carry, compared as the manifest normalizes them. A scoped statement holds a call when
  each of its scopes that the function works along has the call's value among its own; a call that gives no value is
  not within it; a scope the function doesn't work along doesn't narrow it. Where no call's values are known, a
  scoped allow or ask may apply and a scoped deny waits for the call: so it is for which agents a person may use,
  and for the API server's own check of an agent's call to it (§6), which sees no values and leaves a scoped deny
  to the runtime that judged them.
- **After the policy,** a level above the chat's trust still waits for its person
  ([agents](agents.md#6-what-a-program-may-call)).
- **The same rules are evaluated the same way** by the API server, for every call it receives, and by the runtime,
  from the chat's contract, for what never reaches the API server, such as a model calling an agent's function.

**Bounds,** the same for every change to people, groups and policies:

- a person grants only what they hold themselves;
- they change only someone who holds no more than they do;
- an organization always keeps someone who can manage access: an active person who may place people in groups and
  give groups their policies. A change that would leave nobody is undone and refused.

## 4. People and organizations

**An organization** keeps everything of its own apart from every other. It begins in one of three ways:

- **At first run,** the first visitor with the setup code makes the first organization and its administrator. The
  code is `AI_API_SETUP_CODE`: until the first run is done, the server doesn't start without one, and it never writes
  it to its log. Tries are counted per address, 10 in 15 minutes.
- **By sign-up,** where it is open and mail is set up: anyone makes one of their own by following a link mailed to
  them.
- **By the operator,** with the admin tool.

The operator disables and enables organizations; a disabled one's people are signed out and refused. The
organization made at first run is the operator's own, recorded then. `AI_API_OPERATOR_ORG` may name another. An
organization's administrators rename it.

**A person** belongs to one organization, by an email address that is theirs alone in the deployment.

- **They join** by invitation: a link mailed to them. Where no mail is set up, an administrator adds them with a
  temporary password, which they replace before their first session; where mail is set up, only by invitation.
- **Their password** is 10 to 256 characters, starting and ending with no space, kept as bcrypt (cost 12) over
  SHA-256. Forgotten, it is replaced through a link mailed to them. An administrator's reset mails them that link;
  where no mail is set up, it gives a temporary password instead, and signs them out.
- **Disabled,** they are signed out at once and keep everything. An administrator never disables themselves.
- **Removed,** their sessions, keys, chats (with their schedules), memories, webhooks, notification addresses and
  remembered approvals go. Their records, skills and MCP servers pass to a successor the administrator names, in the
  organization, so those who share them keep them; only the secrets they kept to themselves are deleted: a person's
  passwords and accounts are theirs alone. The audit trail keeps their lines. A preview says first what it will
  touch.
- **Their profile** is their name and their preferences (at most 8,000 characters). They see the people and groups
  of their organization, to share with.
- **Who they are,** what the web client asks first: their person, their organization, every action they may take,
  whether they are the operator, and whether speech is set up.

**Groups.** Every organization has Everyone, which holds every person without listing them, and Administrators. Both
stay, keep their names, and are marked as what they are. An administrator makes others, with names of 1 to 80
characters unique in the organization, renames and deletes them, places people in them, and gives them policies.
Deleting a group takes it off its people and out of every agent's rows (§5).

**Two policies are built in:** the platform's own, read-only, and changed with every upgrade.

| Policy | Allows | Given at the start to |
|---|---|---|
| Administrator | every action, every agent's included | Administrators |
| Member | what every person needs: their own chats, records and settings, seeing the agents they may use, their API keys, their own usage and lines of the trail | Everyone |

An administrator writes any other, under a name unique in the organization: to grant more, or to deny.

**One-time links,** for invitations, sign-up and password resets, are one kind:

- a random value kept only as its hash, used once; a link can be looked at before it is used;
- an invitation lasts 7 days, the others 1 hour;
- to one address, a new one no sooner than a minute after the last. A reset or sign-up link goes at most 5 times a
  day to one address, and is asked at most 10 times a day from one network address; an organization sends at most
  100 invitations a day. A new invitation ends the one before it.
- An address that has an account, or none, is answered as any other: nothing says whether it exists.

## 5. Agents

What an organization installs, and where from ([agents](agents.md#10-publishing)).

**A source** is a catalog's address an administrator saved, with a credential if it is private. It belongs to the
organization.

- Its credential is a token, sent as `Authorization: Bearer`, or a header and its value. It is kept encrypted,
  never shown again, and sent only to the source's own host. Changing the address asks for the credential again.
- It is read when saved and when an administrator refreshes it: at most 8 MiB, within a minute. The catalog is kept
  as it was read, with each entry's manifest checked, and nothing in it runs. An administrator sees what each source
  offers against what is installed: not installed, installed, an update, or offered by another source.
- Deleting a source uninstalls its agents.

**Installing.** An administrator reviews one agent of a source's catalog and installs that version.

- The install is pinned to the catalog as it was reviewed: if the source changed since, it is refused and the review
  shown again.
- Each file, the program for each machine and the samples, is downloaded, checked against its digest and size, and
  kept by its digest. From then on the publisher's server is never needed.
- The manifest is checked against the standard's schema and its cross-references. A key it doesn't know is refused.
- A runtime then starts the agent once, with no network, and waits for a `ready` that matches the manifest. Until
  then the agent can't be used; if the check fails, it says why. While no runtime may serve the organization it
  stays unchecked, and an administrator asks for the check again.

**An agent's id is one agent in an organization:** installed from one source only. Another source offering the same
id is refused.

**A version never changes.** Every version an organization installed is recorded for good with its digest: after an
uninstall too, and whatever the source. The same version with other bytes is refused.

**An update** is a new version: the catalog shows it, an administrator reviews and installs it, and the agent keeps
its records and who may use it.

**Uninstalled,** an agent runs no more, and its rows of who may use it go with it. Its records stay their people's,
and installing it again gives them back to it. Its rows start again from none; policies that name its actions
still hold, the built-in Administrator among them, which allows every action: administrators may use it at once.

**Who may use an agent** is kept with the installed agent: rows, in order, each for one of the organization's groups.
Whoever may install agents reads them (`Agents:Agent:access`) and sets them (`Agents:Agent:setaccess`).

```jsonc
{"group_id": "…", "effect": "allow" | "ask" | "deny", "functions": ["*"] | ["draft_send", …],
 "scopes": {"mailbox": ["work"]}}   // optional: only scopes the manifest declares
```

- **A row is statements of its group's people** (§3): its functions, `agent:<id>:<function>`, and what each calls on
  the runtime, `agent:<id>:<function>:*`; `"*"` is `agent:<id>:*`. So a row allowing or asking a function covers what
  it calls, unless another row or a policy denies it.
- **Policies naming agents' actions count too,** evaluated with the rows. Reading the rows also gives the
  organization's groups with how many people each holds, the agent's functions and scopes, and the policies that
  name it, which are changed where policies are.
- **Checked:** the organization's groups, the agent's functions or every one, an effect, scopes it declares with lists
  of values; at most 50 rows. Rows grant only agents' actions, never managing access: the bounds on people, groups
  and policies (§3) are not theirs, and an organization keeps someone who manages access whatever they say.
- **A change** is seen by every server at its next call, and the chats held read their contract again (§7). A group
  deleted leaves no row.

**Who may:** administrators save sources, and install, update and uninstall agents. A person's chats use the agents
whose functions their rows or policies allow or ask: administrators, whose built-in policy allows every action, may
use every installed agent. A person sees the agents they may use and that passed their check, their manifests and
their samples; whoever may install agents sees every installed one.

**Samples:** a person loads an agent's samples as their own records, listed for that agent, once until they remove
them. One call removes exactly what was loaded.

**Runtimes** fetch an installed agent's program by its digest, in pieces of 768 KiB.

## 6. Records

What agents keep for people: secrets, data and files ([agents](agents.md#7-resources)).

**A record** has one shape for the three kinds:

| Field | Is |
|---|---|
| `kind` | `secret`, `data` or `file` |
| `org_id` | its organization |
| `of` | the agent and resource it was made of; none for a person's own file |
| `owner` | the people and groups it belongs to; at first, its maker alone |
| `created_by` | whose it is: the one who changes, shares, hands on and deletes it |
| `agents` | the agents that may use it, each with the resource of its own the record serves: `[{agent, resource}]`; at most 20 |
| `name` | its person's words for it, at most 100 characters; a file's is its file's name, 1 to 255 characters |
| `keys` | plain values, which lists filter on |
| `values` | encrypted values |
| `file` | for a file: its name, type and size, and where its bytes are kept |

- **Every secret and data record is of a resource an installed agent declares.** A file is of one, or a person's
  own: one they uploaded, attached to a chat, or an agent returned, which is marked with the chat and the agent. A
  person's own file reaches an agent only by being handed to a call, or by its person adding the agent to it.
- **Values are encrypted,** each bound to its record's organization, kind and id: copied anywhere else, they don't
  decrypt.
- **A record's fields are at most 512 KiB,** so it fits in one message to a program
  ([sandbox](sandbox.md#5-the-program-protocol)); a text field, at most 20,000 characters.

**The declaration is the manifest's.** A record's fields are checked against its resource as declared in its agent's
installed version. Nothing is copied from the manifest. While the agent is uninstalled, its records' fields can't be
changed, but the records are still read, named, shared, handed on and deleted. An update that adds a required field
leaves existing records as they are, without it, until the agent or its person writes it. A field the new version no
longer declares stays in the record as it was.

**A person** lists, in pages, and reads the records that are theirs or shared with them, without secret values; and
sees the resources of each kind the agents they may use declare. Of the records they made:

- makes, changes, names and deletes them. A data record is made and changed by its person only where its resource's
  `user_access` says `create` and `update`; otherwise only by its agent. A secret is made by either;
- shares them with people of their organization and groups they are in;
- adds an agent they may use to a record, by hand, choosing which of its resources the record serves. It is allowed
  only when the kinds match and the record has every field that resource requires, by the same name and type; a
  file, when it meets that resource's `constraints`. Only a person adds an agent; an agent never does.
- hands a record to another active member, who accepts it before it is theirs, and may withdraw it until then.
  Skills, MCP servers and webhooks are handed on the same way;
- connects an account a resource declares (§8).

They fill a secret an agent asked for on a card (§7). They upload files, and download those they may see: a file's
type, from its name, is checked against its bytes; a person keeps at most `AI_API_FILES_PER_PERSON_GIB` of files
(§9); a download is always an attachment.

An administrator holding `records:manage_any` may delete or hand on any record of the organization, but never share
it or add an agent to it.

**An agent,** through a runtime, within a call for a chat the runtime holds (§2), reaches the records that list it
under that resource and that the chat's person may see, with the `record.*` functions
([agents](agents.md#9-the-standard-field-by-field)).

- Each call is checked again here: the chat uses the agent, the function declares that call on that resource, and
  the person's rules and the run's trust allow it, or its person approved this very call (§7).
- What it makes is the chat's person's, listed for it alone. A file record it makes is one of the person's own
  files, such as one it returned, made of its resource.
- A secret's values reach it only through `record.read`, at level 2; a connected account's, as an access token good
  for at least a minute more, refreshed when it isn't, never its refresh token.
- A secret field left empty puts the resource's declared form before the chat's person (§7); an account to connect
  asks them to connect it.
- A field its manifest reads a host from (`from_secret`, [agents](agents.md#8-the-way-out)) is its person's alone to
  fill in: a `record.create` or `record.update` that writes it is refused.
- A `form` field's answers not to be remembered go to the agent once, and are never kept.

**A file's bytes** are kept in the deployment's storage, local, S3-compatible or Azure Blob, under an id that says
nothing of the file.

**Skills, MCP servers and memories** are not records: they are a person's assistant's (§8).

## 7. Chats

**A chat** is one person's, and nobody else sees it. They make, list (the last changed first, archived ones apart),
rename, archive, restore, clear and delete it. Clearing takes its messages, events, cards, outputs and state, and
keeps its schedules; deleting takes all of it, its schedules and the person's own files attached to its messages
with it. Its settings are one table; a change to them is an event of the chat (below):

| Setting | Is | Default |
|---|---|---|
| `trust` | 0 to 3: the level up to which agents act without asking ([agents](agents.md#6-what-a-program-may-call)) | `1` |
| `agents` | which of the agents its person may use this chat uses: all of them unless it names some; naming none, none | all |
| `model`, `model_id`, `effort` | the model connection its assistant uses, which of the connection's models (its first unless one is picked), and the effort: `low`, `medium` or `high` | the organization's default, its first model, none |
| `time_zone` | for its schedules | `UTC` |
| `turn_budget`, `skills`, `mcp_servers` | the assistant's own: its turn budget, and the skills and MCP servers it reads (§8), chosen as its agents are | `0`, all, all |

**One runtime holds a chat while it is active.**

- When a chat needs a runtime, because its person writes, a schedule is due or an answer arrives, an API server
  places it on one that may serve its organization (§2), isn't draining and has room: one of the organization's own
  first, then the one that held it last, then the least loaded. It records it as the chat's holder. Placing is
  claimed in the database, so two servers never place one chat at once. When none takes it, what the person wrote is
  kept all the same, and the chat says no runtime took it (`runtime_unavailable`).
- Only the holder acts for the chat (§2). A holder that stops answering loses its chats; each is placed again when
  it is next needed.
- The runtime decides when it lets a chat go ([agents](agents.md#2-the-runtime-ends-an-idle-process)). The chat is
  then held by none.
- **Its contract** is what a runtime is given for a chat: the chat's settings, its person and organization, the
  agents it uses (installed, checked, and allowed or asked for its person) with their manifests and programs, the
  rules the person's policies and those agents' rows make for agents' actions (§5) and the approvals they
  remembered, its model connection with its key and the model it uses, the organization's guardrail and blocked
  hosts, and the assistant's own: the person's memories, and the skills and MCP servers it reads, with their
  credentials. When any of it changes, the holder is given it again.
- **Its state** is the assistant's: written by the holder alone, in named parts merged into a document of its own,
  each save the parts that changed (or all of them), up to 4 MiB in all, and never read by the API server.
- **Its outputs,** what each piece of the assistant's work answered, are kept whole, apart from its state, once by the
  hash of their text, up to 2,097,152 characters each: written by the holder, read back a page at a time (100,000
  characters at most), and gone when the chat is cleared or deleted ([assistant](assistant.md#6-the-work-log)).

**Messages.** What the person writes, at most 20,000 characters, with at most 20 files they may see (§6), is kept
and sent to the holder: at most 30 a minute. What the assistant says, the holder writes, with the model that wrote
it and, for a schedule's or webhook's run, the run that posted it. A message is at most 512 KiB. Each is numbered in
its chat, so a chat has one order. Opening a chat gives its last 50 messages and its open cards; earlier ones are
read a page at a time, the newest first (§9).

**Events** are what happens in a chat as it happens: the assistant working or idle, each piece of its work as it
begins, waits and ends (with why, what it was given, its output's first line and ref, the files it returned and the
records it touched), progress notes, messages, cards opened and closed, a schedule added and its runs, the chat
stopped, and the chat's settings changed by its person. A card's `card_closed` keeps what was decided, so a chat read
again shows each question with its answer: `{card_id, kind, answer, at}`, the answer as kept on the card, never a
secret value, or `null` when it expired unanswered. The holder tells what its work does; the rest are written by the
calls they come of: messages, cards opened and closed, schedules added and run, a chat stopped, its settings
changed. An event is at most 512 KiB. Every API server follows the events kept, through one change stream it
resumes where it stopped, and sends those of the chats its browsers have open, so they reach the person whichever
server their browser reached (§1). The last 500 are kept, read again when a browser opens the chat (`AI:Event:list`,
at most 500 after the number given); each is numbered in its chat, so a gap in the numbers is always an event
missed. A piece of work's whole output is read a page at a time by the chat's person (`AI:Output:see`) as by its
holder.

**What only passes,** the assistant's words as they are written, what each of its threads is doing (thinking,
folding earlier messages, or its work), and an agent's screen, never reaches the database:
for as long as a browser has a chat open on a server, that server asks the chat's runtime for them
(`Runtime:Chat:passing`), each ask held until something passes. Each carries the number of the last event kept
before it; one older than an event the browser took already came late, and is dropped.

**Cards** ask the chat's person, and only them:

| Kind | Asks |
|---|---|
| approval | the calls one step of the assistant needs the person for, each an item: an agent's call above its run's trust, or one a policy says `ask` for (§3), or one of their MCP tools above its trust (§8). The person allows all, none, or each, and may remember the answers for each agent, function and record |
| question | what `chat` expects: words, a choice or a file ([agents](agents.md#9-the-standard-field-by-field)) |
| form | a secret's empty fields, a `form` field's questions, or an account to connect (§6) |
| network | `network.open`'s code, purpose and hosts |
| the assistant's own | to save a skill or a memory (§8) |

- A runtime opens a card, but a form, which only an agent's record call opens here (§6); only the chat's person
  answers it, once, at most 20,000 characters, and the answer goes to the holder. Opened, a card tells its person.
- A card waits for its person however long it takes, whoever started the work: it ends answered, or expired when
  its work is stopped. One still open, but the assistant's own, is told to its person again every 12 hours.
- What is typed into a form goes into the record, not the card. An answer not to be remembered goes to the agent
  once.
- A remembered approval is the person's, listed where they can take it back.
- When an agent's call reaches the API server, an approval's item answered yes lets it through only if it was for
  that call, agent, function and operation, and only once.

**Schedules** run a chat's work with nobody present: a function marked `schedulable`, or a prompt for the assistant.

- What: a prompt of at most 4,000 characters, or `agent.function` of an agent the chat uses, with inputs of at most
  16,000 characters. The person sees which functions a chat can schedule.
- When: a cron line in the chat's time zone as it was when the schedule was made, every so many seconds (at least
  60), or once, at a time.
- The person lists, reads, makes, changes when, pauses, resumes, runs now and deletes them, paused ones too: those
  that will run, the soonest first, then those paused and done, a page at a time (§9). The assistant lists (at most
  50), makes and deletes its chat's, through its runtime.
- Each runs at its own trust: its chat's when it is made; its person may set another on the schedules page, above
  the chat's too, and the assistant never. A record call its run makes is checked at that trust.
- A function's schedule may name a `wake` field of its output: a run whose output has it set wakes the assistant,
  with nobody there.
- When one is due, one API server claims it, sets when it runs next, places its chat if no runtime holds it, and
  asks the holder to run it; while its person is disabled or has stopped everything, that run is skipped. The last
  20 runs' outcomes are kept, each told to its person.
- A run that needs its person waits on the card, however long they take, and its person is told
  ([assistant](assistant.md#8-asking-the-person)).
- At most 50 to a chat, and a cap for each person and each organization (§9).

**A person calls one of an agent's functions** in their chat themselves (`AI:Chat:call`, its agent, function
and inputs): its holder runs it now, attended, through the same gate as the assistant's calls
([runtime](runtime.md#5-the-gate)), as a piece of the chat's work whose origin is `person`; what it notes, progress
and screens, reaches the chat as any call's does. It is refused when the chat doesn't use the agent, its person may
not, or the agent has no such function. Its inputs are at most 16,000 characters, and a person makes at most 30 such
calls a minute. The web client opens an agent's live browser this way.

**Stopping.** A person stops a chat: its work is cancelled and its cards expire. Or they stop one call or task of
it, by the id its `work` event gave, and the rest goes on. They may stop everything of theirs,
every chat and schedule, until they resume: their open cards expire, and meanwhile nothing is sent to a runtime for
them but a stop, and a runtime may only write down what their chats had done. An administrator may stop everything
of a person who holds no more than they do (§3), and resume it.

**The live socket,** `/chats/<id>`, is one for each chat open in a browser, with the token as its subprotocol (§2).

- The browser sends the person's words and files (`AI:Chat:input`) and their input on an agent's screen
  (`AI:Chat:screen`), and nothing else; each is answered on the socket. Each is checked
  ([agents](agents.md#9-the-standard-field-by-field)), and limited in size and rate: a frame is at most 64 KiB; 30
  messages a minute; 1 to 64 screen events at a time, 600 a minute. The socket is closed, 4401, at the first input
  after its person signed out or was disabled.
- The server sends the chat's events, from where the browser left off (`after`, the last number it has).
- While a browser has a chat open, its person isn't notified of it (§8).
- A socket it refuses is accepted, then closed with why: 4401 signed out, 4403 no chat of theirs.

**Across chats,** a person sees what waits for them: every open card of every chat, oldest first, a page at a time,
with each chat's title and how many it has open, to answer in one place; how many there are of each kind and how many
chats ask; and their next 100 schedules that are not paused.

**Inspecting a chat.** Its person, and only they, as for opening it, sees everything kept of it in one answer
(`AI:Chat:inspect`): names, refs and counts, never a secret's or a record's value.

| Part | Is |
|---|---|
| the chat | its settings, whether it works, the runtime holding it and since when, or the one that held it last |
| usage | its model calls (§9): totals, by model and by agent, and the last 200, each with when, how long it took and what for |
| processes | its agents' last 100 processes, as its holder tells them: agent, version, runtime, sandbox and machine; when each started and ended, why, and the most memory and the processor's seconds its reports showed. One still running when its runtime is gone ends with it, `runtime_gone` |
| network | what its agents' processes reached through their runtime's proxy, by agent, host and port: connections allowed and refused, bytes out and in, when first and last; the busiest first |
| cards | its last 200, open, answered or expired, with their answers as kept |
| skills, mcp | the person's skills its assistant read, and the MCP tools it called, each counted |
| files | files its person attached, and files its agents' calls returned, each marked with the chat and the agent (§6) |
| records | the records its agents listed, read, made, changed and deleted, by record, each operation counted; a record is named by its file's name, the name its person gave it, or its resource's label |
| schedules | the chat's, and its person's next 20 runs across their chats |
| memories | how many of its person's memories its prompts carry |

What is seen of a chat's work is kept as usage is, and goes by age alone (§9). Its holder tells it in the background,
batched, and never waits for it: what reached each host is told every 30 seconds while a process runs, and as it ends
([runtime](runtime.md#4-agents-processes)).

## 8. Settings

**The organization's** are one table, changed by its administrators, each change on the audit trail:

| Setting | Is | Default |
|---|---|---|
| `blocked_hosts` | hosts no agent may reach, whatever its manifest declares ([agents](agents.md#8-the-way-out)): at most 300 names, `*.example.com` allowed | none |
| `guardrail` | the organization's own rules, added to every chat's instructions; at most 4,000 characters | empty |
| `runtimes` | where its chats may run: `prefer_own` or `own_only` (§2) | `prefer_own` |
| `mcp` | whether people may add MCP servers: `allowed` or `blocked` | `allowed` |
| `webhooks` | whether webhooks may wake chats, and notifications go to outside addresses: `allowed` or `blocked` | `allowed` |
| `notification_text` | `shown`, or `hidden`: every notification says only that something happened | `shown` |
| `speech` | what writes speech down and says replies aloud: the platform's speech service (`service`), a model connection (`model`), or `none` | `none` |
| `speech_connection`, `transcription_model`, `speech_model`, `speech_voice` | for speech: the model connection it uses, the models that write down and speak, and the voice | none |
| `model` | the default model connection | none |

A change is given to every chat its runtimes hold (§7).

**Model connections** are an administrator's: a provider, its address, its key and the models people may pick from it
(at most 100), the first the one a chat uses unless its person picks another, shared with groups. The key is
encrypted and never shown again; changing the address asks for it again. A runtime checks a connection and lists its
models, since runtimes are what call them, a saved one or one as given before it is saved. A person sees the
connections shared with their groups, and the providers and models the platform's catalog knows; whoever may make
connections sees every one. A chat uses the organization's default unless its person picks another they may use.
Deleting the default leaves none.

**OAuth apps** are an administrator's registration of a provider's app, one for each provider: its client id, its
secret and its endpoints, each a public `https://` address. The secret is encrypted and never shown again; changing
an endpoint asks for it again. A resource that declares `oauth` connects only through its organization's
registration for that provider, and only when the endpoints its manifest names are those registered (§6). The
provider sends the person back to `<AI_API_URL>/oauth/callback`, and the web client completes it.

**Notifications** tell a person, when they are not looking, of a card waiting, an answer ready, or a schedule's run
finished or failed.

- They go by web push to their browsers, by email when no browser of theirs takes pushes, and to outside addresses
  they choose, as JSON or a Slack or Teams message, for the events they choose, with the reply where they ask for
  it. Each person turns push, email and the words on or off for themselves; the organization's `notification_text`
  and `webhooks` apply to every way.
- All go through one queue: each claimed once, sent within 10 seconds, tried again for a day after a timeout or a
  server's error, never after a refusal. A push service that says a subscription is gone ends it. Twenty failures in
  a row pause an address, and the last 20 sendings to it are kept for its person.
- A push subscription is kept for its person and browser. A person sends themselves a test, and one to an address.

**Webhooks** let an outside system wake a chat of a person's.

- A person makes one with a name, an instruction, the fields it takes (JSON Schema) and its trust, while the
  organization allows webhooks; at most 25. It gets a chat of its own. Deleted, its chat is archived.
- Its address is `/hooks/<id>`. Its secret is shown once, and may be replaced. A webhook that is off, unknown, of a
  person disabled, or in an organization that blocks webhooks is answered alike, `404`.
- A call carries the secret, or, where the webhook requires it, a signature over its time, its idempotency key and
  its body, taken once within five minutes: `Authorization: Bearer <secret>`; or `X-DecentAI-Timestamp: <seconds>`
  and `X-DecentAI-Signature`, the hex HMAC-SHA256 of `<timestamp>.<idempotency key>.<body>` under the secret. Its
  `Idempotency-Key` is at most 200 characters.
- Its body is JSON of at most 64 KiB, checked against its fields; at most 60 a minute. It is answered `202` with its
  delivery's id. A repeated idempotency key is answered with the first delivery. Its refusals are kept at most one a
  minute.
- Its chat runs at the webhook's trust, 0 to 3, which its person chooses: 0 unless they say. Its person may send it
  a test.
- Its person may let people and groups see it and what it did, and hand it with its chat to another member, who
  accepts.
- What it received and did is kept 30 days.

**Speech** writes a person's words down and says replies aloud, as the organization's `speech` says, within
`AI_API_OUTSIDE_PER_MINUTE` for each person (§9). Audio is at most 10 MB; words said, at most 3,000 characters.

**A person's assistant** keeps, for the core's design:

- **memories:** at most 50, each at most 500 characters; past 50, the oldest goes;
- **skills:** written by the person or saved from a card, a name of 1 to 100 characters and at most 20,000 of text,
  shared with people and groups;
- **MCP servers:** an address, credential headers kept encrypted, and which tools are on, each with its level, 0 to
  3, 2 unless the person changes it; shared with people and groups. One is added only while the organization's `mcp`
  allows it; changing its address asks for the credential again, and clears its tools until they are read again.
  Their tools are read by a runtime, not the API server.

## 9. Calling out, limits and the trail

**Calling out.** Every address the API server was given by people, not by the operator, is reached the same way: a
catalog and the files it names, a provider's OAuth endpoints, a push service, a notification's address, a speech
connection. An MCP server's address is checked the same way, though only runtimes reach it.

- Only a public address, over `https`, unless the operator's `AI_API_INSIDE_HOSTS` names its host or network: such
  an address may also be reached over `http`. An address with a name or password in it is refused.
- The name is looked up once, and the connection goes to the address that was checked; its TLS certificate is
  checked against the name.
- A redirect is followed only after the same checks, at most five. A credential is never sent to a host but its own.
- Every call has a deadline for the whole of it and a limit on what it reads. A slow address slows only the call that
  reached it.
- A failure tells the caller only that the address could not be reached, or what status it answered.

What the operator names is reached as named: the database, the mail server, the speech service, the runtimes and
the issuer.

**Limits:**

| Variable | Is | Default |
|---|---|---|
| `AI_API_INSIDE_HOSTS` | hosts and networks inside the deployment that people's addresses may reach | none |
| `AI_API_CALL_MAX_KIB` | a call on the people's door, and any body but an upload's, a recording's or a webhook's | `1024` |
| `AI_API_UPLOAD_MAX_MIB` | a file uploaded, or kept from a program | `25` |
| `AI_API_CALLS_PER_MINUTE` | calls of one person, their keys' included | `600` |
| `AI_API_OUTSIDE_PER_MINUTE` | calls of one person that reach outside: speech, and test notifications | `10` |
| `AI_API_FILES_PER_PERSON_GIB` | the files one person uploads and keeps | `10` |
| `AI_API_SCHEDULES_PER_PERSON` | the schedules one person keeps | `200` |
| `AI_API_SCHEDULES_PER_ORG` | the schedules one organization keeps | `2000` |
| `AI_API_KEPT_DAYS` | how long the audit trail, usage and what is seen of chats' work are kept | `400` |

- For each person, at most 20 API keys, 25 webhooks, 10 outside addresses for notifications and 50 memories; for
  each chat, 50 schedules.
- Lists come in pages of at most 100 (below).
- For each person, a minute: at most 30 messages written in chats, 600 screen inputs and 30 calls of an agent's
  function (§7). For each webhook, 60 calls a minute (§8). Sign-in, setup and links are limited as §2 and §4 say.
- On the runtimes' door, a call is at most 8 MiB, and a caller without a certificate is refused before its body is
  read.
- How often people call, write in a chat, call a function and act on a screen, and how often a webhook is sent, are
  counted in each server's memory and added to the database once a second: across several servers a burst may pass
  a limit by what isn't added yet, a second's worth from each. Sign-in tries, links, invitations, setup, calls that
  reach outside and replayed signatures are counted at once.
- On the runtimes' door, what a call reads of its runtime and of the chat it acts for is kept for 2 seconds on each
  server: a runtime refused, a person who stopped everything, or a chat placed elsewhere takes up to that long to
  hold on another server.

A call past a size or a rate is answered `413` or `429` (§3), `429` with `retry_after`; one past a count, `400` or
`409`, saying which.

**Lists** that grow with their organization come a page at a time, every one the same way:

- A list read newest first (chats, messages, the trail, what a webhook received, runtimes' events) takes `before`:
  the oldest row the page before gave, by its id, or for chats its time of change. It answers no `next`: a page
  shorter than asked is the last. Chats are listed archived or not (`archived`), each with how many cards it has
  open.
- Any other takes `after`, and answers `next`: what to send as `after` for the page that follows, or null when none
  does. One more row is read than is given, so the last page says so itself. `next` is to be sent back as it is: for
  most lists the last row's id, for schedules a place in their order.
- `limit` is 1 to 100, and 100 when left out: a caller that asks no page gets the first and its `next`.
- `q` searches on the server, case aside, anywhere in the fields each list names, with the filters its page shows.
  Where a page says how many there are, the list answers `total`, how many match what was asked, and `counts`, the
  numbers at its head, whatever was searched.
- Only lists page: what the server checks, permissions above all, reads every group and policy it needs, whatever a
  list shows.
- Short lists come whole: a person's API keys, memories, outside addresses and remembered approvals; the agents,
  sources and app registrations of an organization; the people and groups a person may share with.

| List | `q` searches; filters | In the order | Answers besides |
|---|---|---|---|
| `IAM:User:list` | name, email, a group's name; `group_id`, `status`: `active`, `disabled`, `held` (disabled or stopped) | made | `total`; `counts`: `people`, `administrators`, `held`; `groups` its people are in |
| `IAM:Invitation:list` | email | of its id | `total`; `groups` it places people in |
| `IAM:Group:list` | name; or `ids`, those alone | made | each group's `members`; `total`; `counts`: `groups`, `own`; `policies` its groups give |
| `IAM:Group:get` | | made | its people, a page at a time, and `total` |
| `IAM:Policy:list` | name, an action; or `ids`, those alone | the built-in ones, then made | `total`; `catalog` |
| `Settings:Llm:list` | name, provider, address, a model; `all` | made | `total`; `counts`: `connections`, `models`, `without_models`, `default_name` |
| `Settings:Webhook:list` | name, instruction | made | those shared, a page at a time, with their `total`; one's own (at most 25) and those handed, whole; `counts`: `webhooks`, `on`, `signed`, `shared`, `handed` |
| `Nodes:Node:list` | each word in name, id, note or a label; `state`: `connected`, `away`, `refused`; `scope`: `shared`, `own` | of its id | `total`; `counts`: `total`, `connected`, `own_connected`, `refused`, `shared`, `own`, and of those connected `sandboxes`, `draining` and `chats` |
| `Nodes:Authority:list` | id, kind, a label; `state`: `live`, `spent` | newest first | the nodes the page's tokens enrolled; `total`; `counts`: `live`, `spent` |
| `AI:Schedule:list` | prompt, function, its chat's title; `state`: `active`, `failing`, `paused`, `done`; `chat_id` | those that will run, the soonest first; then paused and done, made | `total`; `counts` of each state and `all`; `chats`, each with its title and how many; `upcoming`, the next to run |
| `AI:Activity:list` | | open cards, oldest first | `total`; `counts` of each kind; `chats_waiting`; `waiting`, how many each chat of the page has open; `chats`, their titles; `schedules`, the next 100 not paused |
| records of each kind, skills, MCP servers | records: `of`, `keys` | made | those handed, whole |

**The audit trail** has a line for every change and every refusal (§1).

- A line says when, who (a person, a runtime, a webhook, or a caller nobody knows), in which organization and chat,
  which endpoint or event, the ids it touched, the outcome (`success`, `refused` or `failed`) and the reason. It
  never holds a secret's value or a body. A runtime also writes down what its chats did, as lines named `chat.…`.
- Refusals of callers nobody knows are counted for each address, a line a minute, not one each.
- Lines are written after the answer, a batch each second: a server that dies loses its last second's.
- Lines are only ever added: the API server's database user may add and read them, never change or remove them.
  They go by age alone, after `AI_API_KEPT_DAYS`.
- A person reads their own lines; an administrator with the right, the organization's, and those of one person.
  Either is filtered by a chat, by what was done (an action or its start, such as `ai:chat`), and by how it ended.

**Usage and monitoring.** Runtimes report what each chat used: tokens in, out, read from the prompt cache and written
to it, and how many of those out were reasoning, for each model connection and agent; for each call, when, how long it
took, and what for: one of the assistant's thoughts, folding its history, or an agent's `model`. A person sees their
own usage by day and model, and each chat's when they inspect it (§7). An administrator sees the organization's: by
person, agent, model and day, with its cost where the model's prices are known; what its runtimes run now,
processes, memory and the processor's cores for each agent; the lines its runtimes wrote; and its trail summed up
by what was done and how it ended. Each looks back 30 days unless asked, at most 400 (the summary, 7).

**Metrics** for an outside monitoring tool: `GET /metrics`, in Prometheus' text format, with the operator's
`AI_API_METRICS_TOKEN` as a bearer token; without the token set, there is no such route. The live runtimes and
sandboxes, and for each organization and agent its processes, their memory and the cores they use.

## 10. Runtimes

**A runtime is enrolled** with a token from the deployment's issuer.

- An organization's administrators make tokens for its own runtimes and sandboxes: each shown once, with a lifetime,
  a number of uses and up to 20 labels. They list them, see what each enrolled, and withdraw them; what a withdrawn
  token enrolled is no longer renewed.
- The operator's administrators also make shared tokens, whose runtimes serve every organization that doesn't keep
  to its own (§2).
- The API server asks the issuer for all of it, gives it a page at a time (§9), and never holds a token's secret.
- They also make a **join code**, shown once: a runtime's token and a sandbox's, made as above, with what another
  computer needs to enroll with them. It is `decentai-join:` and the base64url, unpadded, of
  `{"v": 1, "issuer": "host:port", "api_servers": ["host:port"], "roots": "<roots.pem>", "tokens": {"runtime": "…",
  "sandbox": "…"}}`: the issuer and API servers at their public addresses (`AI_API_NODES_ISSUER`,
  `AI_API_NODES_SERVERS`), and the roots in the server's own identity folder. Without both settings it is refused,
  saying which to set. The DecentAI desktop app takes it under **Lend this computer**.

**A runtime reports itself** every few seconds (§2): where it is reached, the chats it holds, whether it is full, the
id of its start, and its sandboxes, with their room and what runs on them. Nobody fills the list in: a runtime is on it
once it reports. Where it is reached is checked to be an address, and every call to it checks its certificate. No new
chat is placed on a full one, nor on one that answered a chat 503, until it reports room. One whose start id changed
started again and holds nothing: its chats go free, and those that were working are placed again, as for one gone.
Unheard for 15 seconds, a runtime is away and given no chat; for 60, it is gone, and the same befalls its chats, and
its processes still running end, `runtime_gone`. Chats a runtime no longer reports go free once they were placed on
it 30 seconds ago or more.

**An administrator sees** their organization's own runtimes, and the operator's administrators the shared ones too:
what each reports, whether it is connected, away or refused, the organization's chats it holds, and how many of
others'. Of what a shared runtime's sandboxes run, an organization sees its own processes alone. They may:

- **name** one: a name and a note, shown in place of its id;
- **drain** one, or one of its sandboxes, and undo it. No new chat is placed on a runtime that drains, and no new
  process is started on a sandbox that drains; what runs goes on. The runtime learns of it in the answer to its
  report.
- **refuse** one by its id, and allow it again. Every call from it is then refused (§2), its chats are placed
  elsewhere, and the issuer no longer renews its certificate.
- **forget** one that is gone: off the list, until it reports again. One still connected, or holding chats, is not
  forgotten. A refused one stays refused.

## 11. Its container and its data

**Its container:**

| Setting | For |
|---|---|
| `user: 999`; `cap_drop: ALL`, nothing added; `no-new-privileges` | it needs no right of any kind |
| `read_only: true`, with a small `tmpfs` at `/tmp` | nothing is written inside it: what arrives goes straight to storage |
| ports `8000` and `8004` | people, in plain HTTP behind the deployment's proxy, which holds the TLS; runtimes, with mutual TLS |
| its identity folder, at `AI_API_IDENTITY` | its certificate, for the runtimes' door; without one, no runtime can reach it |
| `/healthz`, `/readyz` | alive; and ready: the database answers, and its data is the version this server knows (`503` until then) |

Several may run behind the proxy (§1), with the same settings and keys. With local storage, they share one folder.
A file arriving, a program from a catalog or an upload, is written straight into storage while it is checked; one
that fails its check is removed.

**Its database** is MongoDB 7 or later, as a replica set: one node is enough, and following changes needs one (§7).
Its database user may read and write everything but the trail, where it may only add and read (§9). The database's
administrator makes that user and its role with the admin tool; run again, it brings them up to date, and the
deployment runs it at every start.

**Its collections:**

| Of | Collections |
|---|---|
| people (§2, §4) | `organizations`, `users`, `groups`, `policies`, `sessions`, `api_keys`, `links`, `counters`, `deployment` |
| agents (§5) | `sources`, `agents`, `releases`, `samples` |
| records (§6) | `records` |
| chats (§7) | `chats`, `chat_states`, `messages`, `events`, `cards`, `remembered`, `schedules`, `outputs`; what is seen of their work: `processes`, `traffic`, `uses`, `record_uses` |
| settings (§8) | `models`, `oauth_apps`, `outbox`, `notify_to`, `webhooks`, `deliveries`, `memories`, `skills`, `mcp_servers` |
| the trail (§9) | `trail`, `usage` |
| runtimes (§10) | `runtimes`, `refusals` |

- `deployment` is one document: the operator's organization, whether first run is done, and the data's version.
- Indexes are declared in code and made at start; made again, they change nothing. What expires does so by index:
  sessions, links, counters, notifications not sent within a day, deliveries, the trail, usage and what is seen of
  chats' work. An expired API key is refused, and stays listed until it is revoked.
- The database checks nothing: the code checks what it writes.
- An upgrade's changes to the data are numbered steps. The first server to start runs them once and records the
  version in `deployment`; a server never starts on data newer than it knows.
- Data from before this design is not carried over: a deployment starts empty.

**Its keys.** Values are encrypted with AES-256-GCM under versioned keys: `AI_API_ENCRYPTION_KEYS` holds them all,
as `version:key` pairs separated by commas, each key 32 bytes in base64; `AI_API_ENCRYPTION_ACTIVE` names the version
that encrypts, and the others only decrypt. What is encrypted: records' values, sources' credentials, model
connections' keys, OAuth apps' secrets, MCP servers' credentials and webhooks' secrets. To rotate: add a key, make
it active, re-encrypt with the admin tool, then remove the old one; a value that won't open is left and counted.
Nothing else needs a key: session tokens are random values (§2).

**Its settings,** besides the limits of §9:

| Variable | Is | Default |
|---|---|---|
| `AI_API_ADDRESS` | where the people's door listens | `0.0.0.0:8000` |
| `AI_API_RUNTIMES_PORT` | the runtimes' door | `8004` |
| `AI_API_IDENTITY` | its identity folder | `/run/decentai/api` |
| `AI_API_PROXIES` | the proxies whose forwarded headers it believes | `127.0.0.1` |
| `AI_API_URL` | its own address, as browsers and providers reach it: OAuth's redirect is made of it | required |
| `AI_API_APP_URL` | where people open the web client: the links in mail and notifications are made of it | required |
| `AI_API_ORIGINS` | every origin the web client is served from | `AI_API_APP_URL`, and the desktop app's `http://tauri.localhost` and `tauri://localhost` |
| `AI_API_DATABASE_URI`, `AI_API_DATABASE_NAME` | the database; a URI that still says `change-me` is refused | required, `decentai` |
| `AI_API_ENCRYPTION_KEYS`, `AI_API_ENCRYPTION_ACTIVE` | its keys | required |
| `AI_API_STORAGE` | `local`, `s3` or `azure` | `local` |
| `AI_API_STORAGE_FOLDER` | local storage: one folder every API server shares | `/data/storage` |
| `AI_API_S3_BUCKET`, `_REGION`, `_ENDPOINT`, `_PREFIX`, `_ACCESS_KEY_ID`, `_SECRET_ACCESS_KEY`, `_KMS_KEY_ID` | S3-compatible storage, Google Cloud Storage's included | none |
| `AI_API_AZURE_CONNECTION_STRING`, `_CONTAINER`, `_PREFIX` | Azure Blob storage: the account's connection string, with its key or a SAS | none |
| `AI_API_SMTP_HOST`, `_PORT`, `_USERNAME`, `_PASSWORD`, `_SECURITY`; `AI_API_MAIL_FROM` | mail, sent once both the host and the sender are set; without it, no invitations, sign-up, reset links or mailed notifications (§4). `_SECURITY` is `starttls`, `tls` or `none` | none; port `587`, `starttls` |
| `AI_API_VAPID_PUBLIC_KEY`, `_PRIVATE_KEY`, `_SUBJECT` | web push | none |
| `AI_API_SPEECH_URL` | the platform's speech service | `http://speech:8004` |
| `AI_API_SIGNUP` | `closed` or `open`; open takes sign-ups only where mail is set up | `closed` |
| `AI_API_SETUP_CODE` | the first run's code, required until the first run is done; `change-me` is refused | none |
| `AI_API_OPERATOR_ORG` | the operator's organization | the first one made |
| `AI_API_REFERENCE_CATALOG` | a catalog address the Agents page suggests | none |
| `AI_API_NODES_ISSUER`, `AI_API_NODES_SERVERS` | the issuer's public `host:port`, and the API servers' for runtimes, comma-separated: what a join code names (§10); without both there are no join codes | none |
| `AI_API_METRICS_TOKEN` | the bearer token `/metrics` asks for, at least 24 characters; without it there is no `/metrics` (§9) | none |
| `AI_API_LOG_LEVEL`, `AI_API_LOG_FORMAT` | its log, on standard output: `DEBUG`, `INFO`, `WARNING` or `ERROR`; as `text` or `json` | `INFO`, `text` |

A setting it can't use, a number out of its range among them, stops it at start, saying which.

**The admin tool,** `python -m api_server.admin <command>`, run in the container:

| Command | Does |
|---|---|
| `database --admin-uri <uri>` | makes or updates the database user `AI_API_DATABASE_URI` names, and its role (above) |
| `organizations list`, `create --name … --admin-email …`, `disable --org-id …`, `enable --org-id …` | lists organizations; makes one with its administrator, who signs in with a temporary password and chooses their own; disables one, signing its people out, or enables it (§4) |
| `password <email>` | gives a person a temporary password, and signs them out |
| `reencrypt` | encrypts every value again under the active key |
| `storage <from> <to>` | copies every file and program from one storage to another; `AI_API_STORAGE` then names the new one |
| `intermediate request`, `intermediate install --file …` | the issuer's intermediate certificate: the request its root signs, with its fingerprint, and installing what was signed (§10) |
