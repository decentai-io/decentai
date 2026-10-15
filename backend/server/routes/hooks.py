"""POST /hooks/<webhook id> — an outside system's event, for a person's
standing chat (docs/system/webhooks.md).

Outside the gateway, as /auth is: the caller is no person and has no
session. It proves itself with the webhook's own secret — as a bearer
token, and with a signature where the webhook requires one — and that
secret fires this webhook and nothing else.

The checks are made in the order below, and the first that fails is the
answer. Everything a caller could learn about which webhooks exist is
one answer, 404. Only a body that fits the webhook's fields is recorded
and reaches the chat; the answer is 202 once it is, and the chat hears it
after, so a caller never waits on a model.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from server.custom_logging import CustomLoggerFactory

router = APIRouter()
logger = CustomLoggerFactory.get_logger(__name__)

#: The largest body a webhook takes.
MAX_BODY = 64 * 1024
#: How far a signature's timestamp may be from now, in seconds.
SIGNATURE_WINDOW = 300
#: Events a minute one webhook takes; one process holds the count.
PER_MINUTE = 60

_recent: Dict[str, Deque[float]] = defaultdict(deque)
#: Chats being told, held so the loop keeps the task.
_telling: set = set()


def _answer(status: int, error: str = "", **extra: Any) -> JSONResponse:
    body = {"error": error} if error else {}
    return JSONResponse({**body, **extra}, status_code=status)


def _within_rate(webhook_id: str) -> bool:
    now = time.monotonic()
    seen = _recent[webhook_id]
    while seen and now - seen[0] > 60:
        seen.popleft()
    if len(seen) >= PER_MINUTE:
        return False
    seen.append(now)
    return True


def _owner(webhook: Dict[str, Any]):
    """The webhook's person, as the chat runs, or None when they are
    gone, disabled, stopped, or in an organization that is off or has
    turned webhooks off."""
    from database.stores import OrganizationStore, UserStore
    from server.authentication.access import AccessController

    users, organizations = UserStore(), OrganizationStore()
    person = users.get(webhook.get("user_id", ""))
    if person is None or person.get("status") == UserStore.STATUS_DISABLED:
        return None
    org_id = str(person.get("org_id") or "")
    if org_id != webhook.get("org_id") or not organizations.is_active(org_id):
        return None
    if organizations.safety(org_id).get("webhooks") == "blocked":
        return None
    if users.stopped(person["_id"]):
        return None
    return AccessController.build_user(person)


def _proved(request: Request, secret: str, body: bytes, required: bool) -> str:
    """Why the caller is not the webhook's, or ""."""
    from database.stores.webhooks import signature

    given = request.headers.get("authorization", "")
    token = given[7:].strip() if given.lower().startswith("bearer ") else ""
    if not token or not hmac.compare_digest(token, secret):
        return "The webhook's secret is missing or wrong."
    signed = request.headers.get("x-decentai-signature", "")
    stamp = request.headers.get("x-decentai-timestamp", "")
    if not signed and not required:
        return ""
    if not signed or not stamp.isdigit():
        return "This webhook requires a signature and a timestamp."
    if abs(time.time() - int(stamp)) > SIGNATURE_WINDOW:
        return "The signature's timestamp is too far from now."
    if not hmac.compare_digest(signed, signature(secret, stamp, body)):
        return "The signature does not match the body."
    return ""


async def _tell(webhook: Dict[str, Any], person: Dict[str, Any],
                delivery_id: str, data: Dict[str, Any]) -> None:
    """The event, to the webhook's chat: the runtime is dialed for the
    person, as for a schedule nobody has open, and hears it."""
    from database.stores.webhooks import WebhookDeliveryStore
    from server.setup.app_state import get_runtime_clients

    deliveries = WebhookDeliveryStore()
    frame = {"event": "webhook_event", "delivery_id": delivery_id,
             "webhook": {"id": webhook["_id"], "name": webhook.get("name", ""),
                         "prompt": webhook.get("prompt", "")},
             "data": data}
    try:
        told = await get_runtime_clients().send(webhook["chat_id"], person, frame)
    except Exception as exc:
        logger.warning(f"Webhook {webhook['_id']} not delivered to its chat: {exc}")
        told = False
    deliveries.set_outcome(delivery_id, "delivered" if told else "not delivered",
                           "" if told else "The chat could not be reached.")


@router.post("/hooks/{webhook_id}")
async def receive(webhook_id: str, request: Request):
    from database.stores import AuditStore
    from database.stores.webhooks import (
        WebhookDeliveryStore, WebhookFields, WebhookStore)

    store, deliveries = WebhookStore(), WebhookDeliveryStore()
    webhook = store.col.find_one({"_id": str(webhook_id or "")})
    person = _owner(webhook) if webhook and webhook.get("enabled", True) else None
    if webhook is None or person is None:
        return _answer(404, "No such webhook.")

    def refused(status: int, reason: str) -> JSONResponse:
        deliveries.record(webhook, "refused", status, reason)
        AuditStore().append("webhook.refused", person, chat_id=webhook["chat_id"],
                            resource_refs=[webhook["_id"]],
                            details={"status": status, "reason": reason})
        return _answer(status, reason)

    body = await request.body()
    if len(body) > MAX_BODY:
        return refused(413, f"The body is over {MAX_BODY // 1024} KB.")
    reason = _proved(request, store.secret_of(webhook), body,
                     bool(webhook.get("require_signature")))
    if reason:
        return refused(401, reason)
    if not _within_rate(webhook["_id"]):
        return refused(429, f"At most {PER_MINUTE} events a minute.")
    try:
        data = json.loads(body.decode("utf-8") or "null")
    except (UnicodeDecodeError, ValueError):
        return refused(400, "The body is not JSON.")
    problem = WebhookFields.check(webhook.get("fields") or [], data)
    if problem:
        return refused(400, problem)

    key = request.headers.get("idempotency-key", "").strip()
    earlier = deliveries.earlier(webhook["_id"], key)
    if earlier is not None:
        return _answer(202, delivery_id=earlier["_id"], repeated=True)

    delivery = deliveries.record(webhook, "accepted", 202, idempotency_key=key)
    store.touched(webhook["_id"])
    AuditStore().append("webhook.received", person, chat_id=webhook["chat_id"],
                        resource_refs=[webhook["_id"], delivery["_id"]],
                        details={"fields": sorted(data)})
    task = asyncio.get_running_loop().create_task(
        _tell(webhook, person, delivery["_id"], data))
    _telling.add(task)
    task.add_done_callback(_telling.discard)
    return _answer(202, delivery_id=delivery["_id"])
