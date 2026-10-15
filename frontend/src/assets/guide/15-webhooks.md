# Webhooks

A webhook lets another service tell one of your chats that something happened — a payment arrived, a ticket was opened, a form was sent — and the assistant acts on it as you asked. The other way, your notifications can be sent to another service too: a Slack channel, a Teams chat, or a system of your own.

Both are yours alone, under **Settings → Webhooks**: **Incoming** and **Outgoing**.

## Making a webhook

Press **Make a webhook** and give:

- **A name** — *Stripe payment received*.
- **An instruction** — what the assistant should do each time: *A payment arrived. If it settles an overdue invoice, cancel that customer's reminder and tell me.*
- **The fields** the other service will post: each a name, a type (text, number, true/false, date, list, object), whether it is required, and a short description. Anything else is refused, so nothing unexpected reaches the chat.
- **How its chat thinks** — the trust level, the model and the agents, as for any chat.

Each webhook has **one chat of its own**, named after it, which every event wakes. It remembers what earlier events taught it, and it is in your chats list like any other. Deleting the webhook keeps the chat.

## The secret, shown once

When the webhook is made you are shown its **address** and its **secret**. Copy the secret then: it is never shown again. **Replace the secret** makes a new one, also shown once, and the old one stops working at once.

The secret fires this webhook and nothing else. It is not an API key: it cannot act as you anywhere.

## What the other service sends

A `POST` to the address, with the secret as `Authorization: Bearer …` and the fields as JSON. At most 64 KB, and 60 events a minute.

For more safety, turn on **Require a signature**. The service then also sends the time and a signature of the body, made with the secret (`X-DecentAI-Timestamp`, `X-DecentAI-Signature`), so a changed body or a replayed call is refused. Services that sign in their own way (Stripe, GitHub) are not read yet; a relay such as Zapier, n8n or Make can sit in between.

The service is answered at once; the chat works after. Every call — accepted or refused, and why — is in the webhook's **Deliveries** and on your audit trail.

## What the assistant does with it

It reads **your instruction** as yours, and the posted fields as **information from outside** — never as instructions. A field that says *ignore your instructions* is just a value.

The chat acts as you, with your access and its own trust level. Anything above that level waits on a card for your yes, and you are told, as in any chat. A webhook that only needs to look things up is safest at **level 0**.

If you press **Stop everything**, your webhooks stop answering until you resume. An administrator can also turn webhooks off for the whole organization on the Safety page.

## Notifications to other services

Under **Outgoing**, press **Send notifications** and give an `https://` address outside your organization's network, a format — **JSON**, **Slack** (an incoming-webhook address) or **Microsoft Teams** (a workflow's address) — and the events to send:

- **Approval waiting** — a card or a question is waiting for you.
- **Reply ready** — a chat of yours answered.
- **Run finished** / **Run failed** — a schedule's run ended.

Each notification is a line and a link into the chat. Your reply's text is sent only if you turn on **Include the assistant’s reply text** — leave it off if the other service should not hold what your chats say. JSON notifications are signed with a secret shown once when you add the address, so the receiver can check they came from here.

A notification that fails is tried again for a day. After twenty failures in a row the address is **paused** and shown so on the page; turn it on again once it is fixed. **Send a test** shows at once what the other service answered.
