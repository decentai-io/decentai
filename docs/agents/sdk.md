# What your code is given

Every function is `async def name(self, call)` and returns
`(dict, "success" | "error")`. `call` is everything the platform hands
it, and there is nothing else: no identity of the person, no access to
another agent's records, no key to the model, and no way past the
`resources` your manifest declared for this function.

```python
from decentai_sdk.base import AgentBase, ToolBase, ResourceDenied
```

| | What it is | Needs in the manifest |
|---|---|---|
| `call.inputs` | The inputs, validated and with defaults applied | — |
| `call.resources` | Your records, files and secrets | the function's `resources` |
| `call.progress(text)` | A line the person sees while it runs | — |
| `call.llm(prompt, …)` | The chat's model | `llm: true` |
| `call.show.table(…)`, `call.show.chart(…)` | A table or chart you offer | — |
| `call.post(text, show=…)` | Say something to the person yourself | — |
| `call.ask(question, …)` | Ask the person and wait | — |
| `call.credential(host, fields, …)` | Ask for a login as you work | `credentials: true` |
| `call.propose(code, purpose, …)` | Put code before the person | — (`code: true` to be granted what it names) |
| `call.install(packages)` | Packages for code the person allowed | `code: true` |
| `call.screen` | A screen the person can watch and take over | — (`watch: true` for the header's button) |
| `call.conversation` | The chat's opaque key | — |

Where a function runs without a chat, `show`, `ask` and `propose`
give `None` and `post` gives `False`, so the same code runs everywhere.
`screen.show` still answers `True` — the frame is sent, and goes
nowhere — and `call.credential` is refused (`ResourceDenied`): there is
nobody to type a login.

A scheduled run has its chat for what it tells a person: it can `show`,
`post` and `ask`. It cannot `propose` code (`None`), ask for a login
(refused) or show a screen, and its `call.conversation` is empty.

Anything the platform refuses raises **`ResourceDenied`** with the
reason in words. Catch it where you can do something better; otherwise
let it end the call — the platform turns an exception into an error
result with the reason attached.

## Returning

```python
return {"note_ref": ref, "created": True}, "success"
return {"error": "No notebook named 'work'.", "kind": "not_found"}, "error"
```

A success is checked against `outputs` before anything is shown as
fact. An error's `error` is what the assistant reads and says: name the
rule that refused and why, and write nothing when you refuse.

A result holds what JSON can say: text, numbers, booleans, lists and
objects. One that holds anything else — a date, a set, bytes — is
answered as an error result that says so.

Four keys of a result are the platform's own: `storage_ref`,
`displays`, `denied` and `not_permitted`. One your function returns
under any of those names is taken out, before the result is checked
against `outputs`.

## `call.resources`

```python
records = await call.resources.list_data("note", {"notebook": "work"})
record  = await call.resources.read_data("note", ref)
created = await call.resources.create_data("note", {"title": "Rent", "notebook": "home"})
updated = await call.resources.update_data("note", ref, {"priority": 2})
deleted = await call.resources.delete_data("note", ref)        # True or False
```

A record is `{"resource_ref": "…", "keys": {...}, "values": {...}}`:
both halves come back, `values` decrypted. `update_data` changes the
fields you send and keeps the rest. `list_data` filters are
equality on `keys` fields; `values` cannot be filtered on. The fields you write are split into `keys` and
`values` by the manifest, and checked against it — types, `required`,
`select` options. An update may leave a required field out, and may
not write one empty.

```python
files = await call.resources.list_files("document")
record = await call.resources.read_file("document", ref)
text = record.get("content")                        # present when the bytes are text
raw = base64.b64decode(record["content_base64"])    # always present
saved = await call.resources.create_file("document", "notes.csv", content="a,b\n1,2\n")
saved = await call.resources.create_file("export", "chart.png",
                                         content_base64=base64.b64encode(png).decode())
await call.resources.delete_file("document", ref)
```

A file reaches your function whole, whatever its size up to the
platform's upload limit; large files travel through a folder the
platform opens for your worker, and your code never sees the difference.
A file's row — what `list_files` lists, and what `read_file` and
`create_file` answer with — has `resource_ref`, `filename`,
`file_type` and `file_size` at its top, on the platform and on the
simulator the tests run on. A record handed
to your function the same way — its ref in the call's inputs — is read
by `read_data` wherever it is kept, as a file is. A file a person
attached to the chat is read by its ref under any file resource your
function may `read`, where the ref is in this call's inputs or was the
answer to its `call.ask`; a ref learned any other way is looked for in
your own slot. `create_file` answers with the new file's
`resource_ref`.

```python
secret = await call.resources.use_secret("connection")
token = secret["api_token"]                  # values decrypted, for this call only
```

`use_secret` gives the credential the platform resolves for your slot,
in this order: the one the chat is bound to, else the one granted to
your agent, else the person's default, else the only one they hold.
Several with no default is a refusal, never a guess.

A person may hold several for one slot — two mailboxes. List them and
choose:

```python
accounts = await call.resources.list_secrets("mailbox")
# [{"resource_ref": "…", "name": "…", "keys": {"account": "a@example.com", ...},
#   "is_default": True, "is_bound": False}, ...]
chosen = next(a for a in accounts if a["keys"].get("account") == wanted)
secret = await call.resources.use_secret("mailbox", ref=chosen["resource_ref"])
```

The listing carries no value. A ref your slot could not have been
handed — a credential saved for something else and not granted to your
agent — is refused at `use_secret`, whoever can see it. A good pattern
is an optional `account` input on the functions that act, so "check my
other inbox" works without anybody changing a default.

## `call.progress`

```python
await call.progress("Reading 40 invoices")
```

A short line the person sees while the function runs. Narration, not a
report — nothing is kept.

## `call.llm`

```python
answer = await call.llm(prompt, system="You extract dates. Answer with the date only.")
if answer.cut:
    ...                      # a token cap ended it early: ask for less
```

The chat's own model, through the platform; its key never enters your
process. The answer is text (a `str`) that also carries `stop_reason`,
and `cut` is true when a cap stopped the reply before the model was
done. `max_tokens` is optional — leave it out for a reply that is a JSON
object or a few lines.

A picture beside the prompt — a scan, a screenshot:

```python
answer = await call.llm("Read the total on this receipt.",
                        images=[{"resource_id": "receipt", "ref": file_ref}])
answer = await call.llm("What does this page show?",
                        images=[{"mime": "image/jpeg", "content_base64": jpeg_b64}])
```

A named file is read under your function's file grant. Either way the
picture is checked to be one: a PNG, JPEG, GIF or WebP by its own first
bytes, whatever it is called, at most 5 MiB, and at most sixteen in one
ask. What is not is refused with the reason. A model that cannot see
pictures refuses, and you are told which. Each entry of `images` is a
mapping of one of those two forms: bytes, or a ref as bare text, raise
`ValueError` before the model is asked.

A picture your function carries travels on the worker's own line, which
is 2 MiB long: keep one under about 1.5 MiB (base64 makes it a third
larger), and store a larger one as a file and name it. A line that is
too long ends the worker.

**The model reads; your code decides.** Ask for quotes and structure,
check every quote against the source before showing it as fact, and do
arithmetic in code.

## `call.show`

```python
await call.show.table(rows, columns=["title", "notebook"], title="Notes")
await call.show.chart("bar", labels=["Jan", "Feb"],
                      series=[{"name": "Sales", "values": [120, 90]}],
                      title="Sales by month")
```

An **offer**, not a message: the assistant decides whether the person
sees it, and the page draws it with your agent's name. Plain values in
each cell; at most 500 rows, 24 columns, 200 points, 8 series, and 5
offers a call. Chart types: `bar`, `line`, `pie`. Each returns the
display's id, or `None` where nobody could see it or it could not be
kept. Nothing may depend on an offer being shown.

A title is at most 120 characters and a series' name 60. A table has at
least one row: `call.show.table([])` is refused, so show nothing when
there is nothing. A display is at most 262,144 bytes as it is kept;
over that it is refused, and says to show fewer rows. Each refusal is a
`ResourceDenied`.

## `call.post`

```python
await call.post("Imported 12 notes into 'meetings'.", show=[display_id])
```

Says something to the person now, under the assistant, marked as your
agent's, carrying displays this call offered. The assistant hears it
too and will not repeat it. At most 4,000 characters and three posts a
call: a report worth reading, not narration.

## `call.ask`

```python
answer = await call.ask("Update the existing note, or keep both?",
                        choices=["Update it", "Keep both"])
ref = await call.ask("Attach the signed contract", expects="file")
```

A card in the chat. The person picks a choice or answers in their own
words, and the text comes back — or, with `expects="file"`, they attach a
file and its ref comes back (choices are not offered then). Your timeout
stops while it waits; an unanswered question expires after a day and
answers `None`. At most 1,000 characters and eight choices, each of at
most 100 characters.

Ask **before** you write, and have a safe answer for `None`. A question
lives only as long as the call: if the runtime restarts, the call ends,
and a later answer is told back as expired.

When the person's answer is to stop — a **Stop** among your choices —
say so in what you return: a result whose `outcome` is
`"stopped_by_person"` ends the assistant's turn, and the assistant is
told not to try again unless the person asks. Any other result is one
the assistant may act on, by trying again among other things. `outcome`
is an output like any other: declare it in the function's `outputs`.

```python
if answer == "Stop":
    return {"outcome": "stopped_by_person",
            "summary": "Stopped before sending, as you asked."}, "success"
```

## `call.credential`

For an agent that meets a login it could not know beforehand — a
browser at a sign-in form, a tool that needs a token for a site the
person names. The function declares `credentials: true`.

```python
login = await call.credential("id.example.com", fields=[
    {"name": "email", "label": "Email", "type": "text"},
    {"name": "password", "label": "Password", "type": "secret"},
    {"name": "otp", "label": "One-time code", "type": "secret", "remember": False},
], site="acme.example.com")
if login is None:
    return {"error": "A login for example.com is needed."}, "error"
```

The **domain of the page holding the form** keys the saved login;
`site` says where it is used, because the person's consent is per agent
and per site. The card asks them to type it the first time, to allow
your agent on this site when it is saved already, or to choose between
accounts. A field with `remember: False` is asked every time and stored
nowhere. `account` picks one of several; `refresh=True` asks for
everything again. `None` is a decline, or a day gone. Where there is
nobody to ask — a scheduled run, a test whose `ChatSinks` has no
`credential` —
the call is refused instead (`ResourceDenied`).

The answer is the fields by name, with `host` and `account` beside them.
The values reach your process and nothing else; the model sees labels.

One to twenty fields. A field is `name` and `type` (`text` or
`secret`), with `label` if the name is not what a person should read,
`remember: False` for one never kept, and `required: False` for one
that may be left empty. A name is lowercased and cut at 60 characters,
a label at 80, `account` and `site` at 200; the host is at most 253.

Fields accumulate: a later ask for a token on a host that already holds
a login adds the field, keeps the login, and asks only for the token.
The saved login is an ordinary secret of the person's — shared, handed
over and deleted as any other — and your agent never writes one. A
browser session you want to keep is a data resource of your own,
declared with `storage: values`.

## `call.propose` and `call.install`

For an agent that writes code for what the person asked and wants to
run it.

```python
allowed = await call.propose(
    code, "Totals the orders by month and saves them as a CSV file.",
    language="python",            # or "javascript"
    where="",                     # the site, for a script run in a page
    packages=["pandas"], hosts=["api.example.com"],
    credentials=["Shop token for api.example.com"], files=["orders.csv"])
if allowed:
    folder = await call.install(["pandas"])   # put it on the program's PYTHONPATH
    ...
```

The person sees a card with the purpose, what the code needs, the code
itself, and what the assistant made of it when it read it — whether the
code does what the purpose says. `True` is allowed, `False` declined,
`None` nobody answered. Run the code only on `True`.

Write the purpose for a person who does not read code, and name
everything the code touches: a host or a file the code uses and the card
does not name is what the review is there to find.

Without `code: true` on the function, a card is the person's consent
and nothing else: what the code can reach when it runs is still what
the manifest declared.

With `code: true` on the function, an allowed card is also a **grant
for that call**: the hosts it named are opened on your worker's way out
until the call ends, and `call.install` installs exactly the packages it
named — by the platform, never by your code. Hosts are names
(`api.example.com`, `db.example.com:5432`), never an address and never
every host under a name; packages are a name and, if it matters, a
version (`requests==2.32.3`), with no space inside the name. Anything
else is refused before the card is shown. A declined card grants nothing. Where nothing confines agents
(the agent's page says when), the hosts are not held to the card. A deployment's **Safety** setting may let some
code through without a card, or keep a list of packages; your function
hears `True` either way, and the chat is told what ran.

Limits: code up to 20,000 characters, purpose up to 600, thirty names of
each kind, each name — and `where`, the place the code runs — at most
253 characters.

`call.install` takes the list a card named and answers with the folder
the packages are in. The same list asked for again, in this call or a
later one, is not installed again: the folder it is in is handed back.
The platform installs one list at a time, so an ask may wait while
another agent's packages are installed. An empty list is refused.

## `call.screen`

For an agent that drives something visible — a browser above all.

```python
await call.screen.show(jpeg_bytes, width=1280, height=800)     # each step
for event in call.screen.inputs():                              # mouse, keyboard, wheel
    ...
if call.screen.taken:                                           # the person holds control
    ...
await call.screen.close()
```

Frames go to whoever watches the chat; nothing is recorded. `inputs()`
drains the person's events since last asked, including taking and
handing back control (`{"type": "control", "action": "take"}`);
`wait_input(seconds)` waits for the next; `said()` drains what the
person wrote in the chat while this call runs, so a run can be steered.
A frame may carry `tabs=[{"index", "title", "address", "active"}]`, and a
person's hand on a tab arrives as `{"type": "tab", "action": "switch" |
"close" | "new", "index": n}`. At most twenty tabs are told of.

A frame is a JPEG or a PNG of at most 300,000 bytes. A larger one is
not sent and `show` answers `False`; one that is not the picture it
says it is is dropped by the platform. Either way the function runs on.
`show` takes a JPEG unless told otherwise: pass a PNG with
`mime="image/png"`, or it is one of the frames that are dropped.

| `call.screen…` | |
|---|---|
| `show(image, width, height, mime="image/jpeg", tabs=None)` | one frame; `True` when it was sent |
| `inputs()` | what the person did since last asked, and nothing twice |
| `wait_input(timeout=1.0)` | the same, waiting up to that many seconds for the first |
| `said()` | what the person wrote in the chat since last asked |
| `taken` | whether the person holds control |
| `took()` | whether they took control since last asked, true even when they have handed it back by now |
| `open` | whether a frame has been shown and the screen not closed since |
| `closed` | whether the person closed the screen: a `watch` function streams until it is |
| `close()` | ends the stream |

A person may take control unasked. Honour `taken`: pause, feed their
events to what you drive, and look again when they hand it back. A
function that looks only between its own steps asks `took()` as well:
a take and a release can both arrive inside one step.

A screen is the call's. When your function returns, raises or runs out
of time, the platform tells whoever is watching that it ended, and the
panel closes. `close()` says so sooner, while the function goes on
with something else.

A function with `watch: true` is the one the platform calls, without the
model, when the person opens your agent's screen from the chat's header.
It streams until `call.screen.closed`, and is never offered to the model.
What the person did on the screen by hand — a sign-in above all — is the
function's to keep before it ends: what it showed may be gone by the
next call.

## `call.conversation`

An opaque key, the same for every call in one chat and different in
every other. Keep something alive between calls — a browser a follow-up
should find where it was — under this key and only this key: one worker
serves every chat of the organization that approved the agent. Empty outside a chat. Close what
nobody has used for a while; a thing shown to a person, or held by a
call, is in use.

## Connecting out

Your worker's one way out is the platform's proxy, and its address is in
the environment (`HTTPS_PROXY` and friends). `requests`, `httpx` and
`urllib` use it by themselves: write your HTTP as usual, to the hosts
your manifest declared. A refused host is answered with a 403 that
says why; how that reaches your code depends on the address, below. A
client that ignores the environment's proxy on purpose
finds the platform's under its own name, `DECENTAI_PROXY`. A confined
worker cannot look a name up: the proxy does, so your code does not.

What does not work behind the proxy, and what to do instead:

| In your agent | Behind the proxy |
|---|---|
| `session.trust_env = False`, or a proxy of its own | cut off — use `DECENTAI_PROXY` |
| `socket.getaddrinfo`, to check an address before connecting | refused — the proxy resolves and checks; check only an address written as one |
| a raw socket, or a protocol that is not HTTP | cut off — open it with `Tunnel`, below, and declare the host with its port |
| a browser it starts itself | cut off, unless it is handed the proxy |

A refusal's words name what to add to the manifest — *…did not declare
files.example.com among the hosts it connects to* — so pass them on as
they are.

For a protocol that is not the web's — mail, a database — declare the
host with its port and open the connection through the SDK:

```python
import ssl
from decentai_sdk.net import Tunnel, TunnelRefused

raw = Tunnel.open("imap.example.com", 993, timeout=30)
link = ssl.create_default_context().wrap_socket(raw, server_hostname="imap.example.com")
```

`TunnelRefused` carries the proxy's status and reason: `.status` is
403 for a host or a port your manifest did not declare, and 502 for a
host that did not answer. Where nothing confines agents, `Tunnel.open`
connects straight. `Tunnel.proxy()` is the proxy's address, or `None`
where there is none, and `Tunnel.through(proxy, host, port)` opens the
connection through one you name.

A refusal reaches an HTTP client in one of two ways, by the address it
asked for:

| Address | What your code gets |
|---|---|
| `https://` | An exception, and no response: `requests.exceptions.ProxyError` from `requests`, `httpx.ProxyError` from `httpx`, `urllib.error.URLError` from `urllib`. The reason is in the exception's text. |
| `http://` | A response: status 403, the reason in its body, and the header `X-DecentAI-Refused` with the status. |

So catch the exception around a call to an `https://` address. A 403
that does arrive as a response may be the proxy's or the host's own,
and the header tells them apart.

## The agent class

```python
class NoteAgent(AgentBase):
    def tools(self):
        return [NotesTool(self), ArchiveTool(self)]
```

A tool class sets `id` and defines one `async` method per function,
named as the function's id. A function whose id is a word Python keeps
for itself — `import`, `class`, `global` — is the method with an
underscore after it (`import_`): the manifest keeps the plain id. A
method written without `async` is run and its answer taken all the
same; most of what `call` offers is awaited, so write `async def`.
`self.agent` reaches the agent from a tool, and from the agent
`self.tool("notes")` reaches a tool by its id, `self.manifest` is the
manifest, and `self.agent_id` its id. Both classes have `self.logger`:
what it writes is in the worker's log, under **Settings → Monitoring**. The code declares nothing
about itself: the manifest is handed to it, a method the manifest does
not declare cannot be called, and a package whose code lacks a function
the manifest declares does not load. Both classes may define
`async def close(self)` to release what they hold. Keep state in your
records, not in memory: a worker may be restarted between any two calls.

What your function raises reaches the assistant as a failed call with
the exception's own words, cut at 300 characters: say in them what the
person can do about it.
