# API keys

An API key lets a script act as you: an integration, a job that runs outside the platform.

## What a key is

A key **is you**. Every request it makes is checked against your own permissions, at that moment — a group you were removed from a minute ago governs the next call. It grants nothing you lack, and stops when you are disabled. It is not a delegation and has no scope of its own.

## Making one

Open **Settings → API keys** and press **+**. Give it a name you will recognise later — the script, the machine. The key is shown **once**, with a copy button. Copy it then; the page cannot show it again, because only its hash is kept.

You may hold twenty live keys. Revoke one on the same page when its script is retired; scripts using it are refused from their next call. A revoked key stays listed as a record of what existed.

Keys are made and revoked from a signed-in browser session only. A key cannot make or revoke keys, so a leaked one cannot make itself permanent or lock you out.

## Using one

Send it as a bearer token on the gateway:

```
POST /app
Authorization: Bearer dk_…
Content-Type: application/json

{"endpoint": "Data:Record:list", "data": {"resource_id": "agt_…__task"}}
```

The gateway speaks one shape for everything: an `endpoint` named `Domain:Controller:action` and a `data` object. The same endpoints the pages call are available to a key, subject to your permissions. Some examples:

| Endpoint | What it does |
|---|---|
| `Data:Record:list` | Records you can see, optionally by kind |
| `Data:Record:create` | A record in a declared shape, as `fields` |
| `AI:Audit:List` | Your audit trail, filtered and paged |
| `Agents:Agent:Available` | The agents installed in your organization |
| `AI:Chat:List` | Your chats |

## What a key cannot do

Sign in, change your password, or manage keys. Everything else is what you can do — no more, and never less than the moment before.
