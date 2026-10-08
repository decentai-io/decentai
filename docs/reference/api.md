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

- The AI and Agents controllers, and of Settings the memory and API-key
  ones, answer in an envelope —
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
