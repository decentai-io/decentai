"""The gateway: POST /app {endpoint, data}, for people, scripts and
execution nodes alike.

A request an execution node makes carries its credential
(``X-DecentAI-Node``), or is the node's own as itself; the reply to it
is signed with the Data Core's key over its exact bytes and the request
it answers (api/services/nodes/signing.py), because a node believes
nothing that key did not sign. A credential the Data Core does not
admit is refused before anything else is read. A chat's delegation is
accepted only from the node it was handed to, and only with the number
of the lease that node holds the chat under now (``X-DecentAI-Lease``).
"""

import json
from json import JSONDecodeError

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response

from contracts.node import (
    HEADER_LEASE, HEADER_NODE, HEADER_REQUEST, HEADER_SIGNATURE)
from server.setup.app_state import get_access_controller, get_api_router
from server.custom_logging import CustomLoggerFactory

router = APIRouter()
logger = CustomLoggerFactory.get_logger(__name__)


def _answer(content, status_code: int, node: str = "",
            request_id: str = "") -> Response:
    """The reply: plain for a person, signed for a node."""
    if not node:
        return JSONResponse(content=content, status_code=status_code)
    from api.services.nodes.signing import DataCoreSignature

    body = json.dumps(content, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")
    try:
        signature = DataCoreSignature().reply(node, request_id, body)
    except ValueError as exc:
        logger.error(str(exc))
        return JSONResponse({"error": "The Data Core cannot sign its reply."},
                            status_code=500)
    return Response(content=body, status_code=status_code,
                    media_type="application/json",
                    headers={HEADER_SIGNATURE: signature})


@router.post("/app")
async def application(request: Request):
    node = ""
    request_id = str(request.headers.get(HEADER_REQUEST) or "")
    try:
        access = get_access_controller()
        user = access.verify_request_auth(request)

        node_shared = False
        if user and user.get("principal_type") == "node":
            node = str(user.get("node_id") or "")
        presented = str(request.headers.get(HEADER_NODE) or "").strip()
        if presented and not node:
            admitted = access.admitted_node(presented)
            if admitted is None:
                return JSONResponse({"error": "node not admitted"},
                                    status_code=401)
            node, node_shared = admitted.key, admitted.shared
            if user is not None:
                user = {**user, "presented_node": node}

        if not user:
            return _answer({"error": "unauthorized"}, 401, node, request_id)

        if user.get("principal_type") == "runtime" and (
                not user.get("node_id") or user.get("node_id") != node):
            # A delegation is bound to the node it was handed to: from
            # any other — a node it was never given to, or a request
            # with no node's credential at all — it opens nothing.
            return _answer({"error": "This delegation is bound to another "
                                     "execution node."}, 401, node, request_id)

        if user.get("principal_type") == "runtime":
            # Fencing: every call a node makes for a chat carries the
            # number of the lease it holds the chat under, and only the
            # live lease's number is accepted — so two nodes can never
            # both act for one chat, whatever either believes.
            from database.stores import LeaseStore

            said = request.headers.get(HEADER_LEASE)
            if not LeaseStore().holds(str(user.get("chat_id") or ""), node, said):
                return _answer({"error": "stale_lease: this node no longer "
                                         "holds the chat."}, 409, node, request_id)

            # An organization that keeps to its own nodes is answered on
            # no shared node's request: no contract, no credential and no
            # model key of its leaves the Data Core for one, whatever
            # placement did before the setting changed.
            from database.stores import OrganizationStore

            if node_shared and OrganizationStore().own_nodes_only(
                    str(user.get("org_id") or "")):
                return _answer({"error": "This organization runs its chats on "
                                         "its own execution nodes only."},
                               403, node, request_id)

        try:
            data = await request.json()
        except JSONDecodeError:
            return _answer({"error": "Invalid JSON body"}, 400, node, request_id)

        msg, status_code = await get_api_router().route(data, user)

        return _answer(jsonable_encoder(msg), status_code, node, request_id)

    except Exception as e:
        logger.error(f"Error processing /app request: {e}", exc_info=True)
        return _answer({"error": "Internal server error"}, 500, node, request_id)
