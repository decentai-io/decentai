# The Data Layer

Where everything a person owns is kept: their credentials, the documents
they upload, the knowledge they write for the assistant, and the records
agents keep on their behalf.

Four different subjects, one shape — and a fifth on the same shape,
the remote tool servers a person adds for their own chats
([MCP servers](mcp.md)), which is personal only: never shared, never
handed over, its credential withheld from every read. This document
describes the shape, what each domain adds to it, and the boundaries it
enforces on every read and every write.

## Why there is a shape at all

Each of these could have been built as its own collection with its own
rules. That is the version where sharing a credential and sharing a file
mean subtly different things, where one of them forgets to filter by
organization, and where the fourth one added is the one that leaks.

So the platform has exactly one implementation of *"a document somebody
owns, that somebody else may be allowed to see"*, and every domain is
that implementation plus a small amount of its own. The things worth
getting right — isolation, sharing, encryption, who may edit — are
written once. A domain can add behaviour; it cannot opt out of the
boundaries.

The test of the design is what a new domain has to write. Records are the
pure case: a name, a collection, and the grant that governs sharing.
Nothing else.

The rules themselves live in one place beyond even this layer:
`server/governance/sharing.py` holds the single implementation of "who
may see this, whom may it be shared to, who may change it", configured
by profile — PERSONAL for the records here, INFRASTRUCTURE for the
organization's LLM connections and agent sources. It has its own
document, [sharing.md](sharing.md). The stores live under
`database/stores/` with everything else that touches Mongo; this layer
is the controllers over them.

---

## The document

Every stored resource has the same skeleton.

| Field | Meaning |
| --- | --- |
| `resource_ref` | The document's own identifier — the thing to hold on to |
| `resource_id` | A category label; identifies nothing on its own |
| `type` | Which domain this belongs to |
| `org_id` | The owning organization |
| `owner` | Who may see it: a set of groups and a set of users |
| `keys` | Plaintext, queryable metadata |
| `values` | The encrypted payload |
| `created_by` | The person who made it |
| `created_at`, `updated_at` | Lifecycle timestamps |

The distinction between the two identifiers matters and is easy to get
backwards. A **reference** names one document. An **id** is a label many
documents share — the category an agent declared, or a name a person
chose. Asking for a category returns a set; asking for a reference
returns a document.

What `resource_id` holds, by domain:

| Domain | `resource_id` |
|---|---|
| a secret | the definition it was made from: an agent's is `agt_<ref>__<slot>` |
| a record | `agt_<ref>__<slot>` for an agent's, or the label a person chose |
| a file | the id of the stored bytes, which is one file's alone; a file's slot is in `keys.category` |
| a skill, an MCP server | one constant each (`skill`, `mcp`) |

### Keys and values

The split is the heart of the shape.

**Keys** are plaintext and queryable. They are what a list page shows and
what a filter matches on: a hostname, a title, a provider name. They are safe to show to anyone allowed to know the document
exists.

**Values** are encrypted at rest. They are the substance: an API token,
a skill's body, a record's private fields, a file's generated metadata.
Who reads them back depends on the domain. A record's, a skill's and a
file's are served to whoever may see the document — they are that
person's own data — and an agent reads its records' values at use. A
secret's are the exception: they go only to the runtime acting in a
chat, never back to a person.

Every domain decides which of its fields go where, and that decision is
the domain's most consequential one. A field in keys can be searched and
seen; a field in values cannot be searched at all. Put a credential's
hostname in keys so an administrator can tell two of them apart at a
glance, and its token in values so nobody can read it.

Both halves are bounded — a limited number of entries, bounded name and
value lengths — because a document nobody can render is not more useful
for being larger.

---

## Visibility

Reads are filtered, not checked. Every read on the shared shape composes
its query from three things: the organization, the domain, and the
owner map. No read served to a caller starts from a document id alone
and then asks whether the caller should have it — the query cannot
match a document the caller may not see. What looks a document up by
its id alone is the platform's own work inside one request: the return
from a provider's sign-in finding the credential it is reconnecting,
a transfer finding what changes hands, the count of who else refers to
a file's bytes, and a write reading back the document it has just
written, which the caller was already shown.

This is a deliberate ordering. A check that happens after a lookup is a
check somebody can forget to write; a filter that is part of the lookup
is one they would have to actively remove.

The `owner` map names groups and users. A caller sees a document when
they are one of its users, or a member of one of its groups. There is a
sentinel meaning *everybody in this organization*, which resolves
against the reader's own organization rather than naming a particular
group — so a document shared organization-wide stays correctly shared
when read by anyone, and cannot reach across a tenant boundary.

### Two boundaries on writing

Visibility says who may read. Two further rules say who may *change*
what.

**You cannot share beyond your own access.** When somebody sets a
document's owner, every group and user named is checked: each must exist
in their organization, and each must be somewhere they can already
reach — their own groups, themselves, or a *peer* who shares one of
their explicit groups. Naming the organization-wide sentinel is refused
outright. Otherwise sharing would be an escalation primitive: anyone
could publish their own documents to a group they are not in.

**Only the creator may edit.** Seeing a shared document is not authority
over it. Someone who was given sight of a credential cannot change its
values, re-share it, or delete it. The creator is the document's
steward, and stewardship can be handed to another active member — by the
steward, by a holder of the domain's escape grant, or by the platform
when the steward is deleted. The sharing is kept, with the new steward
in the old one's place: the groups and the other people named stay, and
the previous steward no longer sees a document unless a group still
covers them.

Both rules lift together, for one domain at a time, through a named
grant — *share files organization-wide and maintain files created by
others*, and one equivalent per domain, reaching what its holder can
already see ([sharing](sharing.md#the-escape-grant)). They are separate grants on
purpose: trusting somebody with everyone's files is not the same
decision as trusting them with everyone's credentials.

---

## Encryption

The `values` half is encrypted with an authenticated cipher, and the
stored envelope records the algorithm, the key version, the nonce, and
the ciphertext.

Two properties are worth stating.

**Each blob is bound to its own document.** The document's identifier is
mixed into the encryption as associated data, so ciphertext moved from
one document to another fails to decrypt rather than decrypting into the
wrong place. Copying a row does not copy a working secret.

**Keys are versioned, and several may be live at once.** Every blob
records the version it was written under. Rotation therefore has three
distinct steps: add a new key and make it active, re-encrypt everything
onto it, and only then retire the old key. The middle step is a
deliberate pass over every encrypted field — the data layer's values,
and beside them a model connection's key, a connected app's client
secret and a source's git token — and the set is declared by the stores
themselves (``ENCRYPTED_FIELDS``) rather than listed somewhere, because
a hand-kept list drifts, and the cost of missing one is silent,
permanent loss the next time a key is retired.

### When a value cannot be read

A blob written under a key the deployment no longer holds cannot be
decrypted. The platform's answer depends on who is asking, and the
difference is deliberate.

For **display**, one unreadable document must not blank the page it
appears on. After a half-finished rotation, the rest of a list is still
true and still needed, so the document comes back marked unreadable and
the page renders. What must never happen is silence: a consumer that
treats an unreadable document as an empty one tells somebody their
skill has no body, and sends them to re-author work that is sitting
there intact. Unreadable is not empty, and the difference is a key, not
the content. Nor is an agent's record written over: an edit to one
whose values cannot be read is refused, so the values are there when
the key is back. A person's own free-form record and a skill's body are
not read before they are written, and an edit that sends new values
replaces what was there.

For **use**, there is no such tolerance. When the runtime asks for a
credential to act with, a failure to decrypt is an error, loudly. A model
proceeding with an empty credential is worse than a model that stops.

---

## The domains

### Secrets

Credentials, and the strictest domain. Values are never returned by any
read, under any permission — the only ways out are two dedicated
operations, available exclusively to the runtime acting inside a chat on
someone's behalf, never to a person through the API: `Secrets:Secret:Use`
for the credential granted to an agent's slot, and
`Secrets:Credential:Resolve` for a site's login an agent asked the
person for. What a person can
see of a credential is its plaintext half: which provider, which host,
which account.

Secrets are the one domain whose shape is not the author's to invent.
Each is created against a **definition**.

### Definitions

A definition is a template: the fields a credential of that kind has,
which of them are encrypted, which are required, and what a person should
be shown when filling them in. Definitions are versioned, and versions
are immutable. Publishing a new version does not alter a credential in
place — each records the version it answers to, and is validated
against that version when edited. A definition that changed under
existing instances would silently invalidate them.

**A credential follows its agent's update where it can.** When an
update publishes a new version of a shape, every credential saved under
the old one is moved onto it if that asks nothing new of its owner: the
fields the new shape still has carry over, the ones it dropped go, and
nothing it requires is missing. One the new shape asks more of stays on
its version — shown as *outdated* under Secrets, where opening and
saving it completes the move — and so does a connected account whose
sign-in changed, which signing in again brings up to date. Until then
it is not handed to the agent half-filled: `Secrets:Secret:Use` refuses
a credential of the agent's own shape that lacks something the current
version requires, and says what is missing and where to add it.

A credential **lent** to an agent from another slot was lent because it
had exactly the shape the agent declared. If the agent's update declares
another, the grant stays and the agent is refused the credential at use
— the agent's page says it no longer fits — until it is taken back and
the agent given one of its own.

Definitions are derived, never written by hand: installing an agent
derives them from what its manifest declares, and a login an agent asks
for as it works derives one for that site. The credential form a person
fills in is generated from the reviewed contract rather than written
twice, and a shape no agent declared — which no agent could use — cannot
exist. A version nothing stands on any more is pruned.

Every agent's declaration derives its own **private** definition,
namespaced by the approval's ref, so two agents' notions of "connection"
never collide and no agent lands on a name somebody else's credential is
stored under. A manifest describes the SHAPE of a credential; it never
names one.

Which credential an agent may actually use is a **grant** — a person
pointing one saved secret at one agent's declared slot. A grant is
offered only where the shapes match exactly (name, type, storage,
required, options; labels and help text are wording, not contract), and
it can be taken back. That is how several agents come to read one stored
key without a second copy of it, and why rotating that key stays a single
act.

Declaring a shape is deliberately not access to anything. A manifest used
to be able to name a shared "family" slug and join whatever was stored
under it, which meant an agent from any repository could copy two public
field names and read the credential — and, because the category it asked
with named no agent, the platform could not tell who was asking. An
agent's category is now always `<agent_ref>__<resource_id>`: it names
exactly one approval, so a grant written against that agent is what
answers, and nothing else is.

### Connected accounts

Some credentials are not typed in but granted: a person signs in with a
provider and comes back with tokens. A definition may say so with an
`oauth` block — the provider's short id, its authorize and token URLs,
the scopes, and where the account's identity can be read from — and the
platform then adds the fields it will fill: `account`, `access_token`,
`refresh_token` (encrypted), `expires_at`, `status`. A manifest may
declare no fields of its own for such a credential, and may not
declare those names.

Two secrets are involved, with two owners. The **registration** — the
client id a provider issued to this software and, where it issued one,
the secret — is the organization's, pasted once by an administrator
under *Settings → Connected apps* (`oauth_apps`, one per provider id,
the secret encrypted and write-only; a registration without one names
the app by its id alone). The **grant** is the person's, and is the
credential. The provider id is the join key: an agent whose credential
says `provider: google` connects through the organization's `google`
registration, and a second agent saying the same reuses it.

The registration also records **where the provider is** — its consent,
token and "who am I" addresses — taken from the installed agents that
name it and shown to the administrator who registers it (who chooses,
when agents disagree). An agent whose manifest names other addresses
cannot connect through it: the client secret goes to the token address
and the person's tokens to it and the identity one, and an approved
agent saying `provider: google` with its own server there would
otherwise collect both.

The flow is the platform's, not the agent's. *Connect* asks the
backend for the consent URL (`Secrets:Oauth:Start`), pinned to a
single-use, ten-minute state row with a PKCE verifier and the browser
session that asked; the provider sends the browser to one callback
route, `/oauth/callback`, which spends the state, refuses unless the
same person came back, in the same session where the flow began in one
(a consent link sent to somebody else connects nothing), exchanges the
code, reads the identity and creates
the credential — or, for a reconnect, refreshes the existing one in
place. At use time the backend refreshes an expiring access token
before handing values out, and what an agent receives is the account
and an access token — short-lived where the provider expires it, kept
as never expiring where the provider gives it neither a lifetime nor a
refresh token: the refresh token never leaves the backend. A grant the provider no longer accepts marks the credential
`needs_reconnect` and the agent is told, in words, to have it
reconnected.

The platform's own two credentials are deliberately NOT definitions.
The model key a chat thinks with is an **LLM connection** — the settings
module's own record (`llm_connections`), whose shape the platform knows
by heart — and the token a private repository is read with lives on the
agent source it reads. Both encrypt exactly the way secrets do and are
equally write-only; what they skip is the ceremony of a definition for a
form that was never anybody's to design. Every definition in this
collection is therefore something an agent declared.

An LLM connection is a **provider and the key to it** — which provider,
where it answers, the key — and not a model. The model is chosen where
it is used, from the ones that provider serves: a chat's `llm` block
names the connection (`secret_ref`), the model and, for a model that
thinks before answering, how hard (`reasoning_effort`); agent routing
names a connection and its embedding model (`Settings:Routing`), speech
a connection and its transcription model (`Settings:Speech`). So one key
is pasted once and serves every model its provider has. A connection
carries one `model` of its own, the one it starts with: what a chat that
chose nothing thinks with. When a block is saved the provider and the
address are written from the connection, and the runtime takes them from
the connection again as the turn begins (`Settings:Llm:Use`) — whose key
it is and where it is sent are never a chat's to say. A person's last
pick — connection, model, effort — is kept in their preferences
(`llm_secret_ref`, `llm_model`, `llm_reasoning_effort`) and is what
their next chat starts with.

Which **provider** a connection may name is one list,
`contracts/llm_providers.json`, read by all three sides: the store
accepts a provider that is on it, the page draws its form from it
(`Settings:Llm:Providers`), and the runtime builds the connector for
the protocol the entry names. An entry is an id, a name, a protocol and
the address the form starts with; the connection keeps its own endpoint,
so a person may point it at a gateway of theirs. The protocols are five,
each one connector in `ai_runtime/llms/connector/`: OpenAI's chat
completions (which is also what every OpenAI-compatible server speaks),
OpenAI's Responses, Anthropic's messages, Google's generateContent and
Amazon Bedrock's Converse. The few providers most people look for carry
`popular`, so a page can show them first and the rest behind a search. An
address that differs per customer — Bedrock's region, an Azure resource,
a Cloudflare account — carries a `<blank>` for that part, which the
person fills in; a connection with one left in it is refused, by the
store and again by the runtime. The list is written from
[models.dev](https://github.com/anomalyco/models.dev), the open database
of providers, by `contracts/generate_llm_providers.py` — from the one
file models.dev publishes, `https://models.dev/api.json` — and is a file
in the repository rather than a request made while the platform runs: an
install with no way out lists what any other does, and a new provider
arrives in a commit somebody read. It is refreshed by running the
script and committing what it wrote; nothing runs it by itself. A
provider that speaks one of the five protocols is an entry and no code;
one with a protocol of its own would be a connector as well. A provider reached
only by signing in, or by a credential that is not one string a person
can paste, is not on the list — GitHub Copilot, Google Vertex — and a
model on the person's own machine is the `openai_compatible` entry with
the address typed.

`contracts/llm_models.json`, written by the same script, lists the
models each provider is known to serve, newest first: the provider's own
id, a name to read, what the model is for (`chat`, `embedding` or
`transcription`) and what models.dev says it can do — read a picture,
think before answering and how hard it may be asked to, the sizes of its
window and of one reply. A chat model is one that answers in words and
calls tools; a model that does neither that nor embeds is not listed.
The form asks `Settings:Llm:Providers` for one provider's, of one kind,
and offers them in the model field. It is an offer and never a gate: the
store accepts any model name, so a model newer than the file is typed.

A model may be reached differently from its provider's others — a
gateway that serves Claude over Anthropic's protocol at a second
address, Azure serving it beside its OpenAI deployments. The model's row
then names its own `protocol`, `endpoint` or both, and the runtime's
factory asks the catalog for the route (`LlmProviders.route`) rather
than the provider alone. The model's address may carry the provider's
blanks; they are read out of the address the connection holds. A
connection the person pointed at a gateway of their own is asked as the
provider is, where they said.

### Files

The only domain that owns bytes as well as a document, and the two are
kept in step here.

Upload *is* the create. A file document exists because bytes were stored;
there is no way to mint one describing bytes that were never written,
because a document whose storage location came from a caller is a
document that can point anywhere.

The storage identifier is derived from the bytes, the filename, what
the caller said of the file, and the folder it lands in, and that
folder embeds the uploader. So the same file uploaded twice by one
person, under the same name and for the same purpose, is one record;
under another name, or by two people, it is two — each belonging to the
person who uploaded it. Bytes are removed only when the
last document referring to them goes.

Where bytes actually live is a connector's business. This platform
ships one, a directory on disk; another store is one connector beside
it. Each stored document records which connector wrote it, and reads
follow that record. Containment is the connector's own rule to enforce:
it resolves every path against its root and refuses anything outside,
regardless of what the document it was handed happens to say.

### Records

Structured data an agent keeps for someone — a watcher's memory of what
it has already seen, an agent's preferences. Each kind is a shape the
agent's manifest declares: which fields there are, which are keys and
which are values. A record a person writes into an agent's kind is
checked against that shape exactly as the agent's own writes are, so the
agent never reads back a record its declaration did not promise —
and only where the manifest's `user_access` lets a person create or
edit that kind at all; otherwise the agent's records are read and
deleted by a person, never written. A record of no agent's kind is the
person's own, free-form.

An agent reaches records and files only in the slots its manifest
declares. Listing and creating name the slot, and so does every call
by reference: the runtime sends the slot an agent's call is for, and a
document kept under any other category — another agent's, the person's
own — answers as one that does not exist. One thing reaches across:
a ref the call was **handed**. A file the person attached, or one
another agent stored, is passed to a function in its inputs — or given
by the person in answer to the agent's question — and the function may
read that ref wherever it is kept. It may not write or delete there,
and it cannot hand itself anything: the runtime keeps the set, from the
call's inputs and the person's answers. The assistant's own reads name
no slot either, and see what the person sees.

### Skills

User-authored knowledge for the assistant. A skill keeps its
**substance** — the body — in the encrypted half and its catalog, title
and summary, in the plaintext half.

That places a demand on listing: a list returns the catalog only, and
does not decrypt. Decrypting every body to render a page of titles would
pay for work nobody asked for, and would stake the whole page on every
key still being present. It is also what makes progressive disclosure
possible: a chat carries one line per skill, up to the number the chat
lists (40 unless changed, with the rest counted), and reads a body only
when the assistant judges it relevant.

---

## What is not here

Two things sit outside the data layer, and both boundaries are worth
stating because they look like exceptions.

**Memory** — what the assistant has been told to remember about a
person — belongs to one user and carries no owner map at all. Sharing a
fact about somebody with a group is not an operation that should exist,
so it is not built on a shape that provides one. Being outside means the
capability is absent rather than merely unused.

**Chats and their messages** are a conversation's own records, not
documents anybody shares. They belong to the person who holds the
conversation, and their lifecycle follows the chat rather than an owner
map.

The dividing line is straightforward: the data layer is for documents
people share. Something personal, or something belonging to a
conversation, is kept somewhere its sharing model cannot be reached by
accident.
