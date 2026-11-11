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
| people's | 8000, HTTPS, usually behind the deployment's proxy | people, their scripts, outside systems | a session token or an API key; a webhook's secret |
| runtimes' | 8004, HTTPS with mutual TLS | runtimes | their certificate |

**A person** signs in with their email and password, and gets a session token: a random value the API server keeps
only as its hash.

- Every call carries it in `Authorization: Bearer`. On the live socket it goes as the subprotocol, since a browser
  can't set a header there.
- Each call reads the session, the person and their organization again, so a person disabled, removed or signed
  out is refused on their next call.
- A session lasts 7 days, or 90 when they chose to stay signed in.
- There are no cookies: no other site can act through a person's browser.
- Wrong passwords are counted per account and address together, and per address, so nobody else's tries can shut a
  person out.

**An API key** is for a person's own scripts. It acts as that person, with their rights. It is shown once, kept
only as its hash, and ends when it expires or is revoked. Its expiry is chosen when it is made: at most 365 days.

**A runtime** is known by its certificate, from the deployment's issuer, of kind `runtime`. An administrator can
refuse one by its id, and it is then refused on every call. A certificate that names an organization serves that
organization alone; one that names none is shared, and serves every organization that doesn't keep to its own
runtimes.

- **For a chat it holds,** it acts as that chat's person, and never with more than they may. For an agent's call it
  names the agent: the API server checks that the chat uses that agent, and gives only records that list it.
- **As itself,** for no chat, it reports its own state and fetches the programs it runs.

A runtime that lied could reach no more than the chats it holds and the agents those chats use.

**An outside system** is known by its webhook's secret ([8](#8-settings)).

**A provider's redirect,** when a person connects an account, is known by the state it carries: spent once, and
bound to the person who started it.

**The operator** has no door. They act through the deployment's settings and its admin tool, inside the container.

## 3. Calls, answers and permissions

**A call** has one shape on both doors: `{"endpoint": "Domain:Name:action", "data": {…}}`. On the people's door it
is `POST /app`, in JSON; on the runtimes' door, the communication module's call. Signing in, first run, signing up,
resetting a password and accepting an invitation are endpoints too: the only ones taken without a token.

Other routes exist only where a call can't take this shape: a file's bytes uploaded or downloaded, the live chat
socket, a webhook, a provider's redirect, and the health probes.

**An answer** is `200` with its result. A refusal is its status and `{"error": {"code", "message"}}`: the message is
words for a person, and a refusal may add a field, such as `retry_after`.

| Status | `code` | Means |
|---|---|---|
| 400 | `invalid` | the call can't be carried out as sent; the message names the field |
| 401 | `signed_out` | no valid session, key or certificate |
| 403 | `refused` | not allowed |
| 404 | `not_found` | nothing by that id that the caller may see, whether or not it exists |
| 409 | `conflict` | the state doesn't allow it now; the message says why |
| 413 | `too_large` | larger than the endpoint takes |
| 429 | `too_many` | too many calls; `retry_after` says when to try again |
| 500 | `failed` | the server failed; the message says nothing of how it works inside |

**Every endpoint declares three things,** and nothing else:

- who may call it: a person, a runtime for a chat it holds, a runtime as itself, or anyone before signing in;
- whether it changes anything;
- what the policy editor says of it.

**Permissions** come from a person's groups, Everyone included. A group holds policies, and a policy is a list of
statements, under a name of its own in the organization that an administrator may change:

```
{"effect": "allow" | "deny" | "ask", "actions": ["iam:user:*", …], "scopes": {…}}
```

- **An action** is an endpoint (`iam:user:set_groups`), a right that is no endpoint (`records:manage_any`), or what
  an agent does: one of its functions, or a runtime function it calls on one of its resources.
- **A deny wins,** then an ask, then an allow. Nothing matching is a deny.
- **`ask` and `scopes` are for agents' actions only.** An ask puts the question to the chat's person, who may
  remember the answer. Scopes narrow a function to the values its manifest's authorization scopes allow
  ([agents](agents.md#5-the-manifest)).
- **After the policy,** a level above the chat's trust still waits for its person
  ([agents](agents.md#6-what-a-program-may-call)).
- **The same rules are evaluated the same way** by the API server, for every call it receives, and by the runtime,
  from the chat's contract, for what never reaches the API server, such as a model calling an agent's function.

**Bounds,** the same for every change to people, groups and policies:

- a person grants only what they hold themselves;
- they change only someone who holds no more than they do;
- an organization always keeps someone who can manage access.

## 4. People and organizations

**An organization** keeps everything of its own apart from every other. It begins in one of three ways:

- **At first run,** the first visitor with the setup code makes the first organization and its administrator. The
  code is `AI_API_SETUP_CODE`: until the first run is done, the server doesn't start without one, and it never writes
  it to its log.
- **By sign-up,** where it is open and mail is set up: anyone makes one of their own by following a link mailed to
  them.
- **By the operator,** with the admin tool.

The operator disables and enables organizations; a disabled one's people are signed out and refused. The
organization made at first run is the operator's own, recorded then. `AI_API_OPERATOR_ORG` may name another.

**A person** belongs to one organization, by an email address that is theirs alone in the deployment.

- **They join** by invitation: a link mailed to them. Where no mail is set up, an administrator adds them with a
  temporary password, which they replace before their first session.
- **Their password** is 10 to 256 characters, kept as bcrypt (cost 12) over SHA-256. Forgotten, it is replaced
  through a link mailed to them; where no mail is set up, through a temporary password from an administrator.
- **Disabled,** they are signed out at once and keep everything.
- **Removed,** their sessions, keys, chats, schedules and memories go. What they shared with others passes to a
  successor the administrator names, so its users keep it. Of what they kept to themselves, data and files pass to
  the successor, and secrets are deleted: a person's passwords and accounts are theirs alone. The audit trail keeps
  their lines. A preview says first what it will touch.
- **Their profile** is their name and their preferences.
- **Who they are,** what the web client asks first: their person, their organization, and every action they may
  take.

**Groups.** Every organization has Everyone, which holds every person without listing them, and Administrators. Both
stay, keep their names, and are marked as what they are. An administrator makes others, places people in them, and
gives them policies.

**Two policies are built in:** the platform's own, read-only, and changed with every upgrade.

| Policy | Allows | Given at the start to |
|---|---|---|
| Administrator | every action | Administrators |
| Member | what every person needs: their own chats, records and settings, the agents they may use, their API keys | Everyone |

An administrator writes any other: to grant more, or to deny.

**One-time links,** for invitations, sign-up and password resets, are one kind:

- a random value kept only as its hash, used once;
- an invitation lasts 7 days, the others 1 hour;
- a new one for the same address no sooner than a minute after the last, and a limit a day for each organization and
  each address; a new invitation ends the one before it.

## 5. Agents

What an organization installs, and where from ([agents](agents.md#10-publishing)).

**A source** is a catalog's address an administrator saved, with a credential if it is private. It belongs to the
organization.

- Its credential is kept encrypted, never shown again, and sent only to the source's own host. Changing the address
  asks for the credential again.
- It is read when saved and when an administrator refreshes it. The catalog is kept as it was read, and nothing in
  it runs.
- Deleting a source uninstalls its agents.

**Installing.** An administrator reviews one agent of a source's catalog and installs that version.

- The install is pinned to the catalog as it was reviewed: if the source changed since, it is refused and the review
  shown again.
- Each file, the program for each machine and the samples, is downloaded, checked against its digest and size, and
  kept by its digest. From then on the publisher's server is never needed.
- The manifest is checked against the standard's schema and its cross-references. A key it doesn't know is refused.
- A runtime then starts the agent once, with no network, and waits for a `ready` that matches the manifest. Until
  then the agent can't be used; if the check fails, it says why.

**An agent's id is one agent in an organization:** installed from one source only. Another source offering the same
id is refused.

**A version never changes.** Every version an organization installed is recorded for good with its digest: after an
uninstall too, and whatever the source. The same version with other bytes is refused.

**An update** is a new version: the catalog shows it, an administrator reviews and installs it, and the agent keeps
its records and its permissions.

**Uninstalled,** an agent runs no more. Its records stay their people's, and installing it again gives them back to
it.

**Who may:** administrators save sources, and install, update and uninstall agents. Using an agent is a permission
like any other (§3): a person's chats use the agents whose functions their policies allow. A person sees the agents
they may use, their manifests and their samples.

**Samples:** a person loads an agent's samples as their own records, listed for that agent. One call removes exactly
what was loaded.

**Runtimes** fetch an installed agent's program by its digest, in pieces.

## 6. Records

What agents keep for people: secrets, data and files ([agents](agents.md#7-resources)).

**A record** has one shape for the three kinds:

| Field | Is |
|---|---|
| `kind` | `secret`, `data` or `file` |
| `org` | its organization |
| `owner` | the people and groups it belongs to; at first, its maker alone |
| `agents` | the agents that may use it, each with the resource of its own the record serves: `[{agent, resource}]` |
| `keys` | plain values, which lists filter on |
| `values` | encrypted values |
| `file` | for a file: its name, type and size, and where its bytes are kept |

- **Every secret and data record is of a resource an installed agent declares.** A file is of one, or a person's
  own: one they uploaded, attached to a chat, or an agent returned. A person's own file reaches an agent only by being
  handed to a call.
- **Values are encrypted,** each bound to its record's organization, kind and id: copied anywhere else, they don't
  decrypt.
- **A record's fields are at most 512 KiB,** so it fits in one message to a program
  ([sandbox](sandbox.md#5-the-program-protocol)).

**The declaration is the manifest's.** A record's fields are checked against its resource as declared in its agent's
installed version, or, while the agent is uninstalled, the last version installed. Nothing is copied from the
manifest. An update that adds a required field leaves existing records as they are: the field is asked of the chat's
person the first time the agent needs it, as a missing secret is (§7). A field the new version no longer declares
stays in the record, and is no longer given to the agent.

**A person,** for the records that are theirs or shared with them:

- lists them, in pages; reads them, without secret values; makes, changes, names and deletes them. A file's name is
  its file's; any other record's is the words its person gives it;
- shares them with people of their organization and groups they are in;
- adds an agent to a record, by hand, choosing which of its resources the record serves. It is allowed only when the
  kinds match and the record has every field that resource requires, by the same name and type. Only a person adds
  an agent; an agent never does.
- hands a record to another member, who accepts it before it is theirs, and may withdraw it until then. Skills, MCP
  servers and webhooks are handed on the same way;
- fills a secret an agent asked for, and connects an account a resource declares;
- uploads and downloads files. A file's type is checked from its bytes, and a download is always an attachment.

An administrator holding `records:manage_any` may delete or hand on any record of the organization, but never share
it or add an agent to it.

**An agent,** through a runtime, within a call for a chat the runtime holds (§2), reaches the records that list it
under that resource and that the chat's person may see, with the `record.*` functions
([agents](agents.md#9-the-standard-field-by-field)).

- What it makes is the chat's person's, listed for it alone.
- A secret's values reach it only through `record.read`, at level 2.
- A secret field left empty puts the resource's declared form before the chat's person (§7); an account to connect
  asks them to connect it.
- A `form` field's answers not to be remembered go to the agent once, and are never kept.

**A file's bytes** are kept in the deployment's storage, local or S3-compatible, under an id that says nothing of the
file.

**Skills, MCP servers and memories** are not records: they are a person's assistant's (§8).

## 7. Chats

**A chat** is one person's, and nobody else sees it. They make, list, rename, archive, restore, clear and delete it;
deleting takes its messages, cards, schedules and files with it. Its settings are one table:

| Setting | Is |
|---|---|
| `trust` | 0 to 3: the level up to which agents act without asking ([agents](agents.md#6-what-a-program-may-call)) |
| `agents` | which of the agents its person may use this chat uses: all of them unless it names some; naming none, none |
| `model`, `effort` | the model connection and effort its assistant uses |
| `time_zone` | for its schedules |
| the assistant's own | its turn budget, skills and MCP servers (§8), chosen as its agents are |

**One runtime holds a chat while it is active.**

- When a chat needs a runtime, because its person writes, a schedule is due or an answer arrives, an API server
  places it on one that may serve its organization (§2) and has room, and records it as the chat's holder. Placing is
  claimed in the database, so two servers never place one chat at once.
- Only the holder acts for the chat (§2). A holder that stops answering loses its chats; each is placed again when
  it is next needed.
- The runtime decides when it lets a chat go ([agents](agents.md#2-the-runtime-ends-an-idle-process)). The chat is
  then held by none.
- **Its contract** is what a runtime is given for a chat: the chat's settings, its person and organization, the
  agents it uses with their manifests, the rules the person's policies make for those agents and the approvals they
  remembered, and its model connection with its key. When any of it changes, the holder is given it again.
- **Its state** is the assistant's: written by the holder alone, kept as given in a document of its own, up to 4 MiB,
  and never read by the API server.

**Messages.** What the person writes, with files they attach (their own, §6), is kept and sent to the holder. What
the assistant says, the holder writes. Each is numbered in its chat, so a chat has one order.

**Events** are what happens in a chat as it happens: the assistant working or idle, progress notes, cards opened and
closed, a schedule's runs, an agent's screen. The holder writes them. Every API server follows the events of the
chats whose sockets it holds, so they reach the person whichever server their browser reached (§1). The last 200 are
kept for a browser that comes back; screen frames, and the assistant's words as they are written, are not kept.
Each event kept is numbered in its chat; one not kept carries the number of the last kept, so a gap in the numbers is
always an event missed.

**Cards** ask the chat's person, and only them:

| Kind | Asks |
|---|---|
| approval | to let an agent's call through: its level is above the chat's trust, or a policy says `ask` (§3). The person may remember the answer for that agent, function and record. Or to let one of their MCP tools be called: its level is above the chat's trust (§8); never remembered, since the tool's level is theirs to set |
| question | what `chat` expects: words, a choice or a file ([agents](agents.md#9-the-standard-field-by-field)) |
| form | a secret's empty fields, a `form` field's questions, or an account to connect (§6) |
| network | `network.open`'s code, purpose and hosts |
| the assistant's own | to save a skill or a memory (§8) |

- A runtime opens a card; only the chat's person answers it, and the answer goes to the holder.
- A card expires when its call ends. A question unanswered for a day is answered `null`.
- What is typed into a form goes into the record, not the card. An answer not to be remembered goes to the agent
  once.
- A remembered approval is the person's, listed where they can take it back.
- When an agent's call reaches the API server, an approval lets it through only if it was for that call, agent,
  function and operation, and only once.

**Schedules** run a chat's work with nobody present: a function marked `schedulable`, or a prompt for the assistant.

- When: a cron line in the chat's time zone, every so many seconds (at least 60), or once, at a time.
- The person lists, makes, pauses, resumes, runs now and deletes them, paused ones too. The assistant lists, makes
  and deletes its chat's, through its runtime.
- A function's schedule may name a `wake` field of its output: a run whose output has it set wakes the assistant,
  with nobody there.
- When one is due, one API server claims it, places its chat if no runtime holds it, and asks the holder to run it.
  The last 20 runs' outcomes are kept.
- At most 50 to a chat, and a cap for each person and each organization (§9).

**Stopping.** A person stops a chat: its work is cancelled and its cards expire. Or they stop one call or task of
it, by the id its `work` event gave, and the rest goes on. They may stop everything of theirs,
every chat and schedule, until they resume; meanwhile a runtime may only write down what their chats had done. An
administrator may stop everything of a person.

**The live socket,** `/chats/<id>`, is one for each chat open in a browser, with the token as its subprotocol (§2).

- The browser sends the person's words and files, and their input on an agent's screen. Each is checked
  ([agents](agents.md#9-the-standard-field-by-field)), and limited in size and rate.
- The server sends the chat's events, from where the browser left off.
- A socket it refuses is accepted, then closed with why: 4401 signed out, 4403 no chat of theirs.

**Across chats,** a person sees what waits for them: cards and schedules, in every chat.

## 8. Settings

**The organization's** are one table, changed by its administrators, each change on the audit trail:

| Setting | Is | Default |
|---|---|---|
| `blocked_hosts` | hosts no agent may reach, whatever its manifest declares ([agents](agents.md#8-the-way-out)) | none |
| `guardrail` | the organization's own rules, added to every chat's instructions; at most 4,000 characters | empty |
| `runtimes` | where its chats may run: `prefer_own` or `own_only` (§2) | `prefer_own` |
| `mcp` | whether people may add MCP servers | allowed |
| `webhooks` | whether webhooks may wake chats, and notifications go to outside addresses | allowed |
| `notification_text` | `shown`, or `hidden`: every notification says only that something happened | `shown` |
| `speech` | what writes speech down and says replies aloud: the platform's speech service, a model connection, or none | none |
| `model` | the default model connection | none |

**Model connections** are an administrator's: a provider, its address, its key and the models it offers, shared with
groups. The key is encrypted and never shown again; changing the address asks for it again. A runtime checks a
connection and lists its models, since runtimes are what call them, a saved one or one as given before it is saved. A chat uses the organization's default unless
its person picks another they may use.

**OAuth apps** are an administrator's registration of a provider's app: its client id, its secret and its endpoints.
The secret is encrypted and never shown again; changing an endpoint asks for it again. A resource that declares
`oauth` connects only through its organization's registration for that provider (§6).

**Notifications** tell a person, when they are not looking, of a card waiting, an answer ready, or a schedule's run
ended.

- They go by web push to their browsers, by email, and to addresses they choose, as JSON or a Slack or Teams
  message. Each person chooses theirs; the organization's `notification_text` and `webhooks` apply to every way.
- All go through one queue: each claimed once, sent with a time limit, tried again for a day after a timeout or a
  server's error, never after a refusal. Twenty failures in a row pause an address.
- A push subscription is kept for its person and browser.

**Webhooks** let an outside system wake a chat of a person's.

- A person makes one with an instruction, the fields it takes (JSON Schema), and its chat's settings; a person has a
  limit of them.
- Its address is `/hooks/<id>`. Its secret is shown once, and may be replaced.
- A call carries the secret, or, where the webhook requires it, a signature over its time, its idempotency key and
  its body, taken once within five minutes: `Authorization: Bearer <secret>`; or `X-DecentAI-Timestamp: <seconds>`
  and `X-DecentAI-Signature`, the hex HMAC-SHA256 of `<timestamp>.<idempotency key>.<body>` under the secret. Its
  `Idempotency-Key` is at most 200 characters.
- Its body is JSON of at most 64 KiB, checked against its fields; at most 60 a minute. A repeated idempotency key is
  answered with the first delivery.
- Its chat runs at the webhook's trust, 0 to 3, which its person chooses: 0 unless they say.
- Its person may let people and groups see it and what it did, and hand it with its chat to another member, who
  accepts.
- What it received and did is kept 30 days.

**Speech** writes a person's words down and says replies aloud, as the organization's `speech` says, within a limit
for each person. Audio is at most 10 MB.

**A person's assistant** keeps, for the core's design:

- **memories:** at most 50, each at most 500 characters;
- **skills:** written by the person or saved from a card, shared with people and groups;
- **MCP servers:** an address, credential headers kept encrypted, and which tools are on, each with its level, 0 to
  3, 2 unless the person changes it; shared with people and groups. Their tools are read by a runtime, not the API
  server.

## 9. Calling out, limits and the trail

**Calling out.** Every address the API server was given by people, not by the operator, is reached the same way: a
catalog, a provider's OAuth endpoints, a notification's address, a speech connection.

- Only a public address, unless the operator's `AI_API_INSIDE_HOSTS` names its host.
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
| `AI_API_CALL_MAX_KIB` | a call on the people's door | `1024` |
| `AI_API_UPLOAD_MAX_MIB` | a file uploaded | `25` |
| `AI_API_CALLS_PER_MINUTE` | calls of one person, their keys' included | `600` |
| `AI_API_OUTSIDE_PER_MINUTE` | calls of one person that reach outside: speech, tests, refreshing a source | `10` |
| `AI_API_FILES_PER_PERSON_GIB` | the files one person keeps | `10` |
| `AI_API_SCHEDULES_PER_PERSON` | the schedules one person keeps | `200` |
| `AI_API_SCHEDULES_PER_ORG` | the schedules one organization keeps | `2000` |
| `AI_API_KEPT_DAYS` | how long the audit trail and usage are kept | `400` |

- For each person, at most 20 API keys, 25 webhooks, 10 outside addresses for notifications and 50 memories; for
  each chat, 50 schedules.
- Lists come in pages of at most 100.
- The live socket takes at most 30 inputs a minute.
- On the runtimes' door, a call is at most 8 MiB, and a caller without a certificate is refused before its body is
  read.

A call past a limit is answered `413` or `429` (§3).

**The audit trail** has a line for every change and every refusal (§1).

- A line says when, who (a person, a runtime or a webhook), in which organization, which endpoint or event, the ids
  it touched, the outcome and the reason. It never holds a secret's value or a body.
- Refusals of callers nobody knows are counted for each address, a line a minute, not one each.
- Lines are only ever added: the API server's database user may add and read them, never change or remove them.
  They go by age alone, after `AI_API_KEPT_DAYS`.
- A person reads their own lines; an administrator with the right, the organization's. Either is filtered by a
  chat, by what was done, and by how it ended.

**Usage and monitoring.** Runtimes report what each chat used: tokens in, out, read from the prompt cache and written
to it, and how many of those out were reasoning, for each model connection and agent. A person sees their own usage.
An administrator sees the organization's: by person, agent, model and day, with its cost where the model's prices are
known; and what its runtimes report, processes, memory and events for each agent.

## 10. Runtimes

**A runtime is enrolled** with a token from the deployment's issuer.

- An organization's administrators make tokens for its own runtimes and sandboxes: each shown once, with a lifetime
  and a number of uses. They list them, see what each enrolled, and withdraw them; what a withdrawn token enrolled is
  no longer renewed.
- The operator's administrators also make shared tokens, whose runtimes serve every organization that doesn't keep
  to its own (§2).
- The API server asks the issuer for all of it, and never holds a token's secret.

**A runtime reports itself** every few seconds (§2): where it is reached, the chats it holds, and its sandboxes,
with their room and what runs on them. Nobody fills the list in: a runtime is on it once it reports. Where it is
reached is checked to be an address, and every call to it checks its certificate.

**An administrator sees** the runtimes that serve their organization, and the operator the shared ones too: what each
reports, the organization's chats it holds, and how many of others'. Of what a shared runtime's sandboxes run, an
organization sees its own processes alone. They may:

- **name** one: a name and a note, shown in place of its id;
- **drain** one, or one of its sandboxes, and undo it. No new chat is placed on a runtime that drains, and no new
  process is started on a sandbox that drains; what runs goes on. The runtime learns of it in the answer to its
  report.
- **refuse** one by its id, and allow it again. Every call from it is then refused (§2), its chats are placed
  elsewhere, and the issuer no longer renews its certificate.
- **forget** one that is gone: off the list. A refused one stays refused.

## 11. Its container and its data

**Its container:**

| Setting | For |
|---|---|
| `user: 999`; `cap_drop: ALL`, nothing added; `no-new-privileges` | it needs no right of any kind |
| `read_only: true`, with a small `tmpfs` at `/tmp` | nothing is written inside it: what arrives goes straight to storage |
| ports `8000` and `8004` | people, behind the deployment's proxy, which holds the TLS; runtimes, with mutual TLS |
| its identity folder, at `AI_API_IDENTITY` | its certificate, for the runtimes' door |
| `/healthz`, `/readyz` | alive; and ready: the database answers, and its data is the version this server knows |

Several may run behind the proxy (§1), with the same settings and keys. With local storage, they share one folder.
A file arriving, a program from a catalog or an upload, is written straight into storage while it is checked; one
that fails its check is removed.

**Its database** is MongoDB 7 or later, as a replica set: one node is enough, and following changes needs one (§7).
Its database user may read and write everything but the trail, where it may only add and read (§9). The database's
administrator makes that user once, with the admin tool.

**Its collections:**

| Of | Collections |
|---|---|
| people (§2, §4) | `organizations`, `users`, `groups`, `policies`, `sessions`, `api_keys`, `links`, `counters`, `deployment` |
| agents (§5) | `sources`, `agents`, `releases`, `samples` |
| records (§6) | `records` |
| chats (§7) | `chats`, `chat_states`, `messages`, `events`, `cards`, `remembered`, `schedules` |
| settings (§8) | `models`, `oauth_apps`, `outbox`, `notify_to`, `webhooks`, `deliveries`, `memories`, `skills`, `mcp_servers` |
| the trail (§9) | `trail`, `usage` |
| runtimes (§10) | `runtimes`, `refusals` |

- `deployment` is one document: the operator's organization, whether first run is done, and the data's version.
- Indexes are declared in code and made at start; made again, they change nothing. What expires does so by index:
  sessions, links, counters, screen frames, deliveries, the trail and usage.
- The database checks nothing: the code checks what it writes.
- An upgrade's changes to the data are numbered steps. The first server to start runs them once and records the
  version in `deployment`; a server never starts on data newer than it knows.
- Data from before this design is not carried over: a deployment starts empty.

**Its keys.** Values are encrypted with AES-256-GCM under versioned keys: `AI_API_ENCRYPTION_KEYS` holds them all,
`AI_API_ENCRYPTION_ACTIVE` names the one that encrypts, and the others only decrypt. To rotate: add a key, make it
active, re-encrypt with the admin tool, then remove the old one. Nothing else needs a key: session tokens are random
values (§2).

**Its settings,** besides the limits of §9:

| Variable | Is | Default |
|---|---|---|
| `AI_API_ADDRESS` | where the people's door listens | `0.0.0.0:8000` |
| `AI_API_RUNTIMES_PORT` | the runtimes' door | `8004` |
| `AI_API_IDENTITY` | its identity folder | `/run/decentai/api` |
| `AI_API_PROXIES` | the proxies whose forwarded headers it believes | `127.0.0.1` |
| `AI_API_URL` | its own address, as browsers and providers reach it | none |
| `AI_API_APP_URL` | where people open the web client | none |
| `AI_API_ORIGINS` | every origin the web client is served from | `AI_API_APP_URL` |
| `AI_API_DATABASE_URI`, `AI_API_DATABASE_NAME` | the database | none, `decentai` |
| `AI_API_ENCRYPTION_KEYS`, `AI_API_ENCRYPTION_ACTIVE` | its keys | none |
| `AI_API_STORAGE` | `local` or `s3` | `local` |
| `AI_API_STORAGE_FOLDER` | local storage: one folder every API server shares | `/data/storage` |
| `AI_API_S3_BUCKET`, `_REGION`, `_ENDPOINT`, `_PREFIX`, `_ACCESS_KEY_ID`, `_SECRET_ACCESS_KEY`, `_KMS_KEY_ID` | S3-compatible storage | none |
| `AI_API_SMTP_HOST`, `_PORT`, `_USERNAME`, `_PASSWORD`, `_SECURITY`; `AI_API_MAIL_FROM` | mail; without it, no invitations, sign-up or reset links (§4) | none |
| `AI_API_VAPID_PUBLIC_KEY`, `_PRIVATE_KEY`, `_SUBJECT` | web push | none |
| `AI_API_SPEECH_URL` | the platform's speech service | `http://speech:8004` |
| `AI_API_SIGNUP` | `closed` or `open` | `closed` |
| `AI_API_SETUP_CODE` | the first run's code, required until the first run is done | none |
| `AI_API_OPERATOR_ORG` | the operator's organization | the first one made |
| `AI_API_REFERENCE_CATALOG` | a catalog address the Agents page suggests | none |
| `AI_API_LOG_LEVEL`, `AI_API_LOG_FORMAT` | its log, on standard output, as `text` or `json` | `INFO`, `text` |

**The admin tool,** `python -m api_server.admin <command>`, run in the container: set up the database (its user,
roles and indexes); list, create, disable and enable organizations; reset a person's password; re-encrypt under the
active key; move files between local and S3 storage; install the issuer's intermediate (§10).
