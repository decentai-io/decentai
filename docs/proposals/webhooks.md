# Webhooks — a proposal

> **Status: proposal, for review before it is built.** Nothing here
> exists yet. The decisions marked *decided* were made on 15 October
> 2026; the rest is the design that follows from them.

Two directions, one page (**Webhooks**, under a person's own settings):

- **Inbound** — an outside system posts an event, and a chat of the
  person's wakes and acts on it: a payment arrived, a ticket opened, a
  form was sent.
- **Outbound** — the platform tells an outside system that something of
  the person's happened: an approval is waiting, an answer is ready, a
  run failed.

Both are a person's own (*decided*: any member may create them, for
themselves; outbound is per person). An organization may turn inbound
webhooks off on its Safety page.

## Inbound

### What a person sets up

| Setting | What it is |
|---|---|
| Name | what the person calls it: *Stripe payment received* |
| Prompt | the person's own instruction, read with every event: *A payment arrived. If it settles an overdue invoice, cancel that customer's reminder and tell me.* |
| Fields | the shape the caller must post: each field's name, type (text, number, true/false, date, list, object), whether it is required, and a description — the same field grammar as an agent's records ([manifest](../agents/manifest.md)) |
| Trust level | 0–3, as for any chat, and no higher than the person may give a chat of their own |
| Model | one of the connections the person can see |
| Agents | optionally, only some of those granted to the person |
| Enabled | on or off |

Each webhook has **one standing chat** (*decided*): every event wakes the
same chat, so it keeps what it has learned, its plan and its schedules.
The chat is created with the webhook, named after it, and shown in the
person's chats with a mark saying events arrive there. Deleting the
webhook leaves the chat, as the person's to keep or delete.

### What the caller does

```
POST https://<deployment>/hooks/<webhook id>
Authorization: Bearer <the webhook's secret>
X-DecentAI-Timestamp: 1760518000                 (with a signature)
X-DecentAI-Signature: sha256=<hex HMAC>          (with a signature)
Idempotency-Key: <the caller's id for this event>   (optional)
Content-Type: application/json

{"customer": "Acme", "amount": 1200, "invoice_id": "INV-1043"}
```

The answer is immediate: **202** with a delivery id, once the event is
checked and recorded. The work happens after; a caller never waits on a
model.

### Credentials (*decided*: a bearer token and an HMAC signature, both in v1)

- **A secret of the webhook's own**, shown once when it is made, kept as
  a hash. It fires this webhook and nothing else — unlike an API key,
  which acts as the whole person on every door. Rotating it is one
  button; the old one stops at once.
- **Bearer** — the secret in `Authorization`. Simple, and what most
  tools can send.
- **Signature** — an HMAC-SHA256 of `"<timestamp>.<body>"` keyed with
  the secret, with the timestamp beside it. The person chooses, per
  webhook, to *require* it; then a call without a valid one, or with a
  timestamp more than five minutes off, is refused. It proves the body
  was not changed and stops a captured call being replayed.

### What is checked, in order, before anything is recorded

1. The webhook exists, is enabled, and its owner is active, not stopped
   (stop-everything pauses webhooks), and inbound webhooks are allowed
   in their organization. Otherwise **404** — one answer, so the
   endpoint says nothing about which webhooks exist.
2. The secret, and the signature where required. Otherwise **401**.
3. The rate: at most a set number of events a minute per webhook.
   Otherwise **429**.
4. The body: JSON, at most 64 KB, matching the fields — required ones
   present, types right, nothing undeclared. Otherwise **400**, saying
   which field, and the model never sees it.
5. The idempotency key, where sent: an event already accepted with it
   in the last day answers its first delivery id again, and runs once.

Every call — accepted or refused, and why — is a line on the audit
trail and in the webhook's own delivery list.

### How the event reaches the chat

The backend records the event in the chat's inbox and wakes the chat the
way a schedule does when nobody has it open: it dials the runtime for
the person, with a fresh delegation, and sends a **`webhook_event`**
frame. That frame is the backend's alone — the browser's socket cannot
send it, since that socket builds only the frames of the actions it
knows — and the runtime accepts it only from the backend's dial.

The chat runs **as the person**: their grants, the webhook's trust level,
its model and its agents. A call above the trust level waits on a card
for the person's yes, as in any chat; the person is told there is one
by push or email.

**The posted data is data, never instructions.** The assistant reads the
person's prompt as the instruction, and the posted fields as a separate
block marked as an event from an outside system, as it reads what an
agent returns. A field saying *ignore your instructions* is a value in a
field. This is the paper's rule
([the idea](../paper/02-the-idea.md#the-email-that-says-ignore-your-instructions))
applied to a door that is open to the internet, which is why trust
level and agents are set per webhook, and why a webhook that only needs
to read is best left at level 0.

## Outbound

### A subscription

| Setting | What it is |
|---|---|
| Address | https only; an address inside the deployment's own network is refused, as an agent's would be |
| Format | plain JSON, **Slack** (an incoming-webhook URL), or **Microsoft Teams** (a workflow's URL) |
| Events | which of the events below |
| Include the reply | off by default (*decided*); on, a *reply ready* event carries the assistant's reply text, not only a link |
| Secret | made by the platform, to sign each delivery (JSON format) |

### Events, to start

- **approval waiting** — a card is waiting for the person's yes
- **reply ready** — a chat answered while nobody was watching
- **run finished**, **run failed** — a job, a schedule's run, or an inbound
  webhook's run, and how it ended

### Delivery

- A POST with the event, the chat's name and a link into the chat;
  never a secret, never the conversation, and the reply text only where
  the subscription asks for it.
- JSON deliveries are signed as inbound calls are (`X-DecentAI-Signature`,
  `X-DecentAI-Timestamp`), so a receiver can check they came from here.
- Retried with backoff for a day on a timeout or a 5xx; a 4xx is not
  retried. After repeated failures the subscription is paused, and the
  person told.
- Each subscription keeps a delivery list — sent, failed and why — and
  a **Send a test** button.

## Who may do what

New actions, in the Members role (*decided*: any member, for
themselves): creating, editing, rotating and deleting one's own inbound
webhooks and outbound subscriptions, and reading their deliveries. An
organization's **Safety** page gains *People may receive webhooks* — on
by default — which turns every inbound webhook of the organization off
at once.

## What it does not do, in v1

- **Provider-specific signatures.** Stripe, GitHub and Slack sign in
  their own ways; v1 checks DecentAI's. A provider that cannot send a
  bearer token or our signature is reached through a relay such as
  Zapier, n8n or Make, until its own scheme is added.
- **Many chats per webhook.** One standing chat each; a new chat per
  event can come later as an option.
- **Organization-wide outbound events.** Per person only.
- **Agents receiving webhooks themselves.** An agent declaring in its
  manifest that it accepts events is a later step.

## What is built

The backend: two stores (webhooks, subscriptions) and two delivery logs;
the public `/hooks/<id>` route, outside the gateway like `/auth`; the
checks above; the outbound sender with its retries; the `webhook_event`
frame. The runtime: the frame, delivered to the chat as an event the
assistant reads as data. The web app: the Webhooks page. The docs and
the paper: the door, its checks and its limits. Tests for each check,
each refusal, the injection rule, and delivery.
