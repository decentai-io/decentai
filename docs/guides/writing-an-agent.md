# Writing an agent

The complete walkthrough — the folder, every manifest key, everything
the SDK gives, the tests, trying an agent on your own computer,
publishing — is in [docs/agents/](../agents/README.md). The
key-by-key contract the platform enforces is
[The manifest](../agents/manifest.md). This
page is what a developer of the platform needs beside those: the
harness, where an agent connects, a credential obtained by signing in,
and the mistakes the reviewer catches.

## Where to start

[`examples/note/`](../../examples/note/) is the place to begin: one
agent that uses every feature the platform can enforce. Beside it,
[docs/agents/](../agents/README.md) has every manifest key and
everything the SDK gives, how to try an agent on your own computer
([a DecentAI of your own](../agents/developing.md#a-decentai-of-your-own)),
and the checklist to run before asking anyone to approve it.

The [`decentai-agents`](https://github.com/decentai-io/decentai-agents) repository holds the agents the project
publishes, each with a README naming its limits and what it needs.
Worth reading for a pattern:

| Agent | Shows |
|---|---|
| Notebook | every feature the platform enforces, in one agent: records with `user_access`, files, a schedulable function |
| Gmail, Outlook | a connected account by `oauth`; level-3 sends; a watch that wakes a schedule only for new mail |
| Mail | a non-HTTP protocol through the proxy (`decentai_sdk.net.Tunnel`), hosts with ports |
| Documents, Sheets, Slides | reading uploads by reference, producing files as bytes and reading them back |
| Tasks, Expenses | a model reading text with every quote checked, decimal money, dates only when exact |
| Browser | a person's sign-in asked for on a card (`call.credential`), the live screen (`call.screen`) |
| Code | a program shown on the code card (`call.propose`) and its packages installed (`call.install`) |

Read a manifest and its tool files side by side; the shape is the same
in every one.

## The harness

An agent is tested in a real worker, over the real protocol, against
the simulator — no backend, no browser. In the agents repository:

```python
# tests/conftest.py builds every catalog agent into one shared
# environment once per session and yields them by id.

from ai_runtime.execution.executor import FunctionExecutor
from sim.resources import InMemoryResourceProvider

def test_a_bad_date_is_refused(agents):
    executor = FunctionExecutor(provider=InMemoryResourceProvider())
    result, status = asyncio.run(executor.invoke(
        agents["tasks"], "tasks.tasks.create",
        {"title": "x", "due": "next week"}, chat_level=1))
    assert status == "error" and "YYYY-MM-DD" in result["error"]
```

- `InMemoryResourceProvider(secrets={"<agent>__<slot>": {...}})` stocks
  a credential; `call.resources.use_secret("<slot>")` returns its values.
- `chat_level` is the chat's trust; a function above it is denied
  unless the executor was given an `approver`.
- A function with `llm: true` takes the model from
  `FunctionExecutor(llm=…)`: an async callable from messages to text,
  which a test scripts.
- Uploads are simulated with `provider.create_file("chat_attachment",
  name, bytes)`; the returned reference is what the function receives.
- External services are stubbed on loopback and pointed at through the
  credential's test-only fields, so the agent's HTTP client is the real
  one.

Run with the platform on the path:

```bash
PYTHONPATH=<path to the platform repository> python -m pytest tests -q
```

The first run builds the shared environment under `tests/`; delete it
after changing dependencies or the SDK, or the workers load a stale
copy.

## What the reviewer catches

Each of these has failed a real manifest; the validator's message names
the rule.

| Mistake | Rule |
|---|---|
| a helper reads a record the calling function did not declare | every operation a function uses is declared on that function |
| a property schema with `description` | hints go in the function's description |
| `additionalProperties: {}` | must be a boolean |
| an output six levels deep | five levels at most; flatten to a top-level list |
| a field named `on` | YAML reads it as a boolean; the same for `yes` and `no` as option values unless quoted |
| a description containing `: ` unquoted | YAML reads a mapping |
| `schedulable: true` on a level-2 function or one with `llm: true` | the clock runs reads and ordinary changes only |
| a `secret` typed field in keys | encrypted fields live in values |
| `family` on a secret | refused; a credential is granted, never claimed by name |
| a host written as `https://api.example.com`, `api.example.com:443` or an address | a host is a name and only a name |
| `from_secret` naming an encrypted field | the field is a `string` in keys; where an agent connects is shown at approval |

And things the platform enforces at run time that a test should pin:

- a read returns `keys` and `values`, decrypted; only `keys` can be
  filtered on (`list_data`);
- a partial `update_data` keeps the fields it did not send;
- files are bytes as `content_base64` in both directions; text as
  `content` is only for text; a file of any size the platform stores
  arrives whole, however large (the wire is not the cap);
- a function above the chat's level is denied without an approver;
- a function's output is validated against its declared schema before
  the platform shows it as fact.

## Conventions the published agents keep

Money as decimals to the cent, never converted between currencies.
Dates as `YYYY-MM-DD` or nothing; an ambiguous day/month is not read. A
model's extraction checked against the text verbatim and marked
verified or assumed. A produced file read back before it is returned.
An original never modified. An unknown never guessed — it is returned
as an ask. Reads at level 0, record writes at 1, file creation at 2,
anything that leaves the platform at 3.

## Where it connects

Every published agent says where it connects, in a `network` block
(the manifest reference has the whole of it):

```yaml
network:
  hosts:
    - graph.microsoft.com
    # where a download is redirected to
    - "*.sharepoint.com"
```

Three things to get right:

- **Name what the code calls, not what the manifest mentions.** The
  consent and token addresses in an `oauth` block are the platform's to
  call, and a link the agent only shows to a person is not a
  connection.
- **Name where a redirect lands.** A provider that answers a download
  with a redirect to another host — Graph and Box both do — has the
  agent connect to that host too.
- **Say nothing, and the agent is shown as connecting anywhere.** An
  agent that works only on what the platform hands it says
  `hosts: []`; one whose work is the open web says `hosts: any`.

Where the platform confines agents, the list is enforced: the worker's
one way out is the platform's proxy. `requests`, `httpx` and `urllib`
take it from the environment and need nothing. What does not work
behind it, and how to tell:

| In the agent | Behind the proxy |
|---|---|
| `session.trust_env = False`, or a proxy of its own | cut off — use `DECENTAI_PROXY` |
| `socket.getaddrinfo`, to check an address before connecting | refused — the proxy resolves and checks; check only an address written as one |
| a raw socket, or a protocol that is not HTTP | cut off — open it with `decentai_sdk.net.Tunnel`, and declare the host with its port (`imap.example.com:993`) |
| a browser it starts itself | cut off, unless it is handed the proxy |

A refused connection arrives as a proxy error whose message says why:
*…did not declare files.example.com among the hosts it connects to*.
Pass it on as it is; it names what to add to the manifest.

## A credential obtained by signing in

A secret with an `oauth` block is connected by signing in with the
provider instead of being typed. The platform runs the round trip,
keeps the refresh token, refreshes before handing the access token
over, and records whose account it is. The block carries what is the
provider's to know; the organization's client id and secret are
registered once per `provider` under Settings → Connected apps.

```yaml
oauth:
  provider: google
  authorize_url: https://accounts.google.com/o/oauth2/v2/auth
  token_url: https://oauth2.googleapis.com/token
  scopes: [openid, https://www.googleapis.com/auth/gmail.modify]
  authorize_params: {access_type: offline, prompt: consent}
  identity: {url: https://openidconnect.googleapis.com/v1/userinfo, field: email}
```

That is the common shape, Google's and Microsoft's. A provider that
departs from it says how, and only in these words:

| key | default | for |
|---|---|---|
| `scope_param` | `scope` | Slack's user token scopes go in `user_scope` |
| `scope_separator` | a space | Slack and Todoist join scopes with `,` |
| `token_auth` | `body` | `basic`: the client id and secret in HTTP Basic (Notion) |
| `token_format` | `form` | `json`: a JSON token request (Notion) |
| `token_path` | the top | a dotted path to the object holding the tokens (Slack's `authed_user`) |
| `identity.method` | `GET` | `POST` for an RPC-style "who am I" (Dropbox) |
| `identity.headers` | none | headers that call insists on |
| `identity.source` | `url` | `token`: read `field` from the token response (Notion's owner) |

`identity.field` is a dotted path either way. A token the provider
issues with neither an expiry nor a refresh token lives until it is
revoked (Slack, Todoist, Notion): it is stored as never expiring and
handed out as it is.

Agents that share a provider share a person's connection only by being
granted the same saved credential, and a credential holds the scopes of
the definition it was connected through. So every agent for one
provider declares the same scope list; the catalog's Google and
Microsoft agents each list the union.

## Publishing

Commit the folder and the catalog file, add the repository as a source
on the marketplace, install, grant. A new version is a new commit, a
refresh of the source, and a new approval. The platform keeps the bytes
it approved; the repository can move or vanish and nothing installed
changes. See [Agent code distribution](../system/agent-code.md).
