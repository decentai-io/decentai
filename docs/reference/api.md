# The API

Everything the web app does, a script can do, through the same doors and
under the same permissions.

## Signing in

```
POST /auth/login
{"email": "you@example.com", "password": "…"}
```

Sets an `access_token` cookie that names a server-side session, valid
for a week — ninety days when the login carried `remember: true`, which
also keeps the cookie past the browser being closed — or until logout.
A login answered 403 with `change_required` is a password an
administrator handed over: `POST /auth/password/first` with `email`,
`current_password` and `new_password` replaces it and signs in. `GET /auth/me` returns the identity and the
actions it holds — what the web app uses to decide which controls to
show. `POST /auth/logout` ends the session.

### Standing an organization up

`GET /auth/setup` answers `setup_required` (nobody is in this
deployment yet), `setup_code_required` and `signup_open`. While setup is
required, `POST /auth/setup` with `org_name`, `name`, `email`,
`password` — and `code`, where one is required — makes the organization
with that person as its administrator, and signs them in; answered 409
once anybody is in the deployment. Where sign-up is open,
`POST /auth/signup` with the same four fields sends a link to the
address and answers the same whoever the address belongs to;
`POST /auth/signup/check` with its `token` says what it will create, and
`POST /auth/signup/confirm` with the `token` creates it and signs in. A
refusal of one field names it in `field`.

A native app has no cookie jar to trust. Signing in with
`"client": "native"` in the body (on login, on the first password, on
accepting an invitation or resetting a password, on the first run or a
sign-up link) answers with the same
session token in the body instead — `access_token`, with `expires_at` —
for the app to keep in the device's own store and present as
`Authorization: Bearer …` on every request and on the chat socket. It
is the same session: signed out, disabled or expired the same way. One
door tells the two apart: API keys are made and revoked only with the
cookie, so the app may list them and is refused the rest.

For a script, an **API key** is simpler: make one on Settings → API
keys and send it as `Authorization: Bearer dk_…`. It resolves to you,
with your permissions checked on every request.

## The gateway

One endpoint carries every operation:

```
POST /app
{"endpoint": "Domain:Controller:action", "data": {…}}
```

The name is the permission: the caller must hold the action
`domain:controller:action` (lowercased) somewhere in their policy
chain, or the answer is 403. The full list, with what each grants, is
[the actions reference](actions.md).

Four response shapes exist:

- The AI and Agents controllers, and of Settings the memory, API-key,
  webhook and outbound ones, answer in an envelope —
  `{"version": …, "request_id": …, "status": "success", "data": {…}, "error": null}`
  — and fail with `"error": {"code": …, "message": …}` in the same
  envelope, the message at most 2048 characters. `request_id` is the
  caller's own: sent inside `data`, it comes back on the answer, and is
  empty when none was sent. `version` is the envelope's
  (`contracts/version.py`), `1` today; nothing compares it yet.
- The identity and data-layer controllers, and the rest of Settings
  (model providers, routing, safety, connected apps, notifications,
  speech), answer the payload directly — `{"resources": […]}`,
  `{"user": {…}}` — and fail with `{"error": "…"}`.
- The gateway's own refusals, made before any controller is reached —
  an action the caller does not hold (403), an endpoint that is missing
  from the body or does not exist, a failure inside a controller (500) —
  are `{"title": …, "type": "error", "messages": [{"description": …}]}`,
  with no `error` key.
- Two are made before the gateway and answer `{"error": "…"}`: a body
  that is not JSON (400, `Invalid JSON body`), and a JSON body that is
  not an object (500, `Internal server error`).

The HTTP status says which happened. A script reads
`error.message` from the first shape, `error` — the sentence itself —
from the second and the fourth, and `messages[0].description` from the
third.

### Worked calls

Records you can see, of one kind:

```
{"endpoint": "Data:Record:list",
 "data": {"resource_id": "agt_a5884e7850a645bc958b__task"}}
```

The category is `<agent ref>__<slot>`; the refs of installed agents
come from `Agents:Agent:Available`, and the kinds each declares from
`Data:Record:shapes`.

A record in a declared shape:

```
{"endpoint": "Data:Record:create",
 "data": {"resource_id": "agt_…__task",
          "fields": {"title": "Send the quotation", "owner": "Dana",
                     "due": "2026-09-10", "status": "open"}}}
```

Fields are validated against the manifest exactly as the agent's own
writes are: unknown fields are refused, required ones must be present,
types must match.

Your audit trail, filtered and paged:

```
{"endpoint": "AI:Audit:List",
 "data": {"event_types": ["execution"], "text": "tasks",
          "since": "2026-09-01T00:00:00Z", "limit": 50}}
```

The answer carries `next_before`; pass it back as `before` for the next
page.

Your chats: `AI:Chat:List`. A chat's messages: `AI:Message:List` with
`chat_id`. What your chats have set in motion, across all of them:
`AI:Activity:List`, which takes no chat and answers `schedules`,
`approvals` waiting, `jobs` running and whether your work is `stopped`.

## Files

Upload is a multipart form:

```
POST /upload
file=<bytes>
additionalData={"endpoint": "Files:File:upload", "folder": "uploads"}
```

or, for a chat's attachment, `AI:Chat:UploadFile` on the gateway with
`chat_id`, `filename` and `content_base64`. Download is
`GET /download/{resource_ref}`, owner-filtered like any read.

## Webhooks

An outside system posts an event to a person's webhook
([webhooks](../system/webhooks.md)). The door is outside the gateway, as
`/auth` is: the caller is no person and has no session, and proves
itself with the webhook's own secret, which fires that webhook and
nothing else.

```
POST /hooks/{webhook_id}
Authorization: Bearer dws_…
X-DecentAI-Timestamp: 1760518000
X-DecentAI-Signature: sha256=<hex>
Idempotency-Key: evt_1043
Content-Type: application/json

{"customer": "Acme", "amount": 1200, "invoice_id": "INV-1043"}
```

| Header | |
|---|---|
| `Authorization` | `Bearer <secret>` — always |
| `X-DecentAI-Timestamp` | Unix seconds, within five minutes of the server's clock; with a signature |
| `X-DecentAI-Signature` | `sha256=` and the hex HMAC-SHA256, keyed with the secret, of `"<timestamp>.<body>"` — the timestamp, a dot, and the body's exact bytes. Required where the webhook requires it; checked wherever it is sent |
| `Idempotency-Key` | optional, at most 200 characters (a longer one is refused, 400): a key accepted in the last day answers its first delivery again and runs nothing |

The first check that fails is the answer, each `{"error": "…"}` but the
first:

| Status | Body | When |
|---|---|---|
| 202 | `{"delivery_id": "whd_…"}`, and `"repeated": true` for a key seen before | checked and recorded; the chat hears it after |
| 404 | `{"error": "No such webhook."}` | no such webhook, or it is off, or its person is disabled or stopped, or their organization is off or does not allow webhooks — one answer for all |
| 413 | | more than 64 KB of body |
| 401 | | the secret missing or wrong; a required signature missing; a timestamp too far off; a signature that does not match |
| 429 | | more than 60 events in a minute for this webhook |
| 400 | | not JSON, not an object, a field not declared, a required one missing, or one of the wrong type — the message names it |

Computing the signature, in a shell:

```
SECRET='dws_…'; TS=$(date +%s)
BODY='{"customer":"Acme","amount":1200,"invoice_id":"INV-1043"}'
SIG=$(printf '%s.%s' "$TS" "$BODY" | openssl dgst -sha256 -hmac "$SECRET" -hex | sed 's/^.* //')
curl -X POST "https://<deployment>/hooks/whk_…" \
  -H "Authorization: Bearer $SECRET" \
  -H "X-DecentAI-Timestamp: $TS" -H "X-DecentAI-Signature: sha256=$SIG" \
  -H "Content-Type: application/json" --data "$BODY"
```

and in Python:

```python
import hashlib, hmac, json, time, urllib.request

secret = "dws_…"
body = json.dumps({"customer": "Acme", "amount": 1200,
                   "invoice_id": "INV-1043"}).encode()
stamp = str(int(time.time()))
mac = hmac.new(secret.encode(), stamp.encode() + b"." + body, hashlib.sha256)
request = urllib.request.Request(
    "https://<deployment>/hooks/whk_…", data=body, method="POST",
    headers={"Authorization": f"Bearer {secret}",
             "X-DecentAI-Timestamp": stamp,
             "X-DecentAI-Signature": f"sha256={mac.hexdigest()}",
             "Content-Type": "application/json"})
print(urllib.request.urlopen(request).read())
```

Sign the bytes you send: a body re-encoded after signing does not match.

**Notifications going out** are signed the same way. A subscription in
the JSON format receives a POST with `Content-Type: application/json`,
`X-DecentAI-Event` (`approval.waiting`, `reply.ready`, `run.finished`,
`run.failed`, or `test`), `X-DecentAI-Timestamp` and
`X-DecentAI-Signature` over `"<timestamp>.<body>"`, keyed with the
subscription's secret (`dss_…`, shown once when it was made). A
receiver computes the same HMAC over the raw body it received, compares
in constant time, and refuses a timestamp far from its clock. The body
is `{"event", "chat": {"id", "title"}, "line", "link", "at"}`, with
`"reply"` where the subscription asked for the reply's text. Answer
2xx; a 5xx, a 408, a 429 or no answer in ten seconds is tried again for
a day. Slack and Teams subscriptions are sent in their own shapes,
unsigned.

The pages' doors, on the gateway, are `Settings:Webhook:*` and
`Settings:Outbound:*` ([actions](actions.md#webhooks)), answering in
the envelope.

## Health

`GET /healthz` says the process is up; `GET /readyz` that the database
answers; `GET /status` says who the caller is (`user_id`, `org_id`,
`email`, `user_name`), or 401.

## The chat door

A chat is driven over a WebSocket, not the gateway. `AI:Chat:Open` on
the gateway creates or resumes the chat and answers its contract — the
model, the agents, what it may do — and where its socket is; the web
app then opens `/chats/{chat_id}` with its session cookie.

The browser's socket has a vocabulary of its own, in the gateway's
envelope. Three things go in:

| Sent | With | For |
|---|---|---|
| `{"endpoint": "AI:Chat:Input", "data": {…}}` | `text`, `attachments`, `client_message_id` | a message; the same `client_message_id` sent again is the same message |
| `AI:Chat:Screen` | `call_id`, `events` (at most 64) | acting on a browser an agent shows ([the live screen](../system/live-screen.md)) |
| `AI:Chat:Watch` | `action` | asking to see, or to close, that browser |

One thing comes out: `{"endpoint": "AI:Chat:Event", "data": {"event": …}}`,
carrying what the runtime tells the chat's audience — narration,
messages, approval cards, questions, frames. The event names are in
[the session door](session-door.md), which is the next hop: the
backend's own dial to the runtime, under a credential a browser never
holds.

Answering an approval card and stopping a chat are not socket frames:
they are `AI:Approval:Decide` and `AI:Chat:Stop` on the gateway, so
that each is checked and recorded before the runtime hears of it. The
socket carries no authority: what the chat may do is the backend's
contract for that chat, which the runtime reads again at every turn.

## What a script cannot do

Sign in with a key, change a password with one (`POST /auth/password`
answers 403 to a key: it is changed from a signed-in browser), manage
keys, or reach the runtime directly. The runtime has no public surface; it is dialed by the
backend under a delegation minted per chat, and that delegation is
fenced to a fixed list of gateway actions — the runtime column in the
actions reference.
