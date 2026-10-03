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
also keeps the cookie past the browser being closed — or until logout. `GET /auth/me` returns the identity and the
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
[the actions reference](../reference/actions.md).

Two response shapes exist, by controller family:

- The AI, Agents and Settings controllers answer
  `{"request_id": …, "data": {…}}` and fail with
  `{"request_id": …, "error": {"code": …, "message": …}}`.
- The identity and data-layer controllers answer the payload directly —
  `{"resources": […]}`, `{"user": {…}}` — and fail with
  `{"error": "…"}`.

The HTTP status says which happened; a script reads `error` from either
shape.

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
`chat_id`. A chat's activity: `AI:Activity:List`.

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
answers; `GET /status` names the deployment for a signed-in caller.

## The chat door

A chat is driven over a WebSocket, not the gateway: the web app opens
`/chats/{chat_id}` with its session cookie and speaks the frames
described in [the session door](../reference/session-door.md)
— a user message in, narration and messages out, approval cards and
their decisions. The door carries no authority: what the chat may do is
the backend's contract for that chat, computed at connection.

## What a script cannot do

Sign in with a key, change a password, manage keys, or reach the
runtime directly. The runtime has no public surface; it is dialed by the
backend under a delegation minted per chat, and that delegation is
fenced to a fixed list of gateway actions — the runtime column in the
actions reference.
