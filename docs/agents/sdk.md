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
give `None`, `post` gives `False`, and `screen.show` gives `False`, so
the same code runs everywhere. A scheduled run has its chat.

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

## `call.resources`

```python
records = await call.resources.list_data("note", {"notebook": "work"})
record  = await call.resources.read_data("note", ref)
created = await call.resources.create_data("note", {"title": "Rent", "notebook": "home"})
updated = await call.resources.update_data("note", ref, {"priority": 2})
deleted = await call.resources.delete_data("note", ref)        # True or False
```

A record is `{"resource_ref": "…", "keys": {...}, "values": {...}}`:
both halves come back, `values` decrypted. `list_data` filters are
equality on `keys` fields; `values` cannot be filtered on. The fields you write are split into `keys` and
`values` by the manifest, and checked against it — types, `required`,
`select` options.

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
`read_file` also gives `filename` and the file's type. A file a person
attached to the chat is read by its ref under any file resource your
function may `read`. `create_file` answers with the new file's
`resource_ref`.

```python
secret = await call.resources.use_secret("connection")
token = secret["api_token"]                  # values decrypted, for this call only
```

`use_secret` gives the credential the platform resolves for your slot.
A person may hold several for one slot — two mailboxes. List them and
choose:

```python
accounts = await call.resources.list_secrets("mailbox")
# [{"resource_ref": "…", "name": "…", "keys": {"account": "a@example.com", ...},
#   "is_default": True, "is_bound": False}, ...]
chosen = next(a for a in accounts if a["keys"].get("account") == wanted)
secret = await call.resources.use_secret("mailbox", ref=chosen["resource_ref"])
```

The listing carries no value. A good pattern is an optional `account`
input on the functions that act, so "check my other inbox" works without
anybody changing a default.

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

A named file is read under your function's file grant. A model that
cannot see pictures refuses, and you are told which.

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
display's id, or `None` where nobody could see it. Nothing may depend on
an offer being shown.

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
answers `None`. At most 1,000 characters and eight choices.

Ask **before** you write, and have a safe answer for `None`. A question
lives only as long as the call: if the runtime restarts, the call ends,
and a later answer is told back as expired.

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
everything again. `None` is a decline, nobody to ask, or a day gone.

The answer is the fields by name, with `host` and `account` beside them.
The values reach your process and nothing else; the model sees labels.

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

With `code: true` on the function, an allowed card is also a **grant
for that call**: the hosts it named are opened on your worker's way out
until the call ends, and `call.install` installs exactly the packages it
named — by the platform, never by your code. Hosts are names
(`api.example.com`, `db.example.com:5432`); packages are a name and, if
it matters, a version (`requests==2.32.3`). Anything else is refused
before the card is shown. A deployment's **Safety** setting may let some
code through without a card, or keep a list of packages; your function
hears `True` either way, and the chat is told what ran.

Limits: code up to 20,000 characters, purpose up to 600, thirty names of
each kind.

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
"close" | "new", "index": n}`.

A function with `watch: true` is the one the platform calls, without the
model, when the person opens your agent's screen from the chat's header.
It streams until `call.screen.closed`, and is never offered to the model.

## `call.conversation`

An opaque key, the same for every call in one chat and different in
every other. Keep something alive between calls — a browser a follow-up
should find where it was — under this key and only this key: one worker
serves every chat of the deployment. Empty outside a chat.

## Connecting out

Your worker's one way out is the platform's proxy, and its address is in
the environment (`HTTPS_PROXY` and friends). `requests`, `httpx` and
`urllib` use it by themselves: write your HTTP as usual, to the hosts
your manifest declared. A refused host comes back as an HTTP 403 whose
body says why.

For a protocol that is not the web's — mail, a database — declare the
host with its port and open the connection through the SDK:

```python
import ssl
from decentai_sdk.net import Tunnel, TunnelRefused

raw = Tunnel.open("imap.example.com", 993, timeout=30)
link = ssl.create_default_context().wrap_socket(raw, server_hostname="imap.example.com")
```

`TunnelRefused` carries the proxy's status and reason. Where nothing
confines agents, `Tunnel.open` connects straight.

## The agent class

```python
class NoteAgent(AgentBase):
    def tools(self):
        return [NotesTool(self), ArchiveTool(self)]
```

A tool class sets `id` and defines one `async` method per function;
`self.agent` reaches the agent from a tool. Both classes may define
`async def close(self)` to release what they hold. Keep state in your
records, not in memory: a worker may be restarted between any two calls.
