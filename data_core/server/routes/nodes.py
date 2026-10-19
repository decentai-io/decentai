"""WS /nodes/link — where execution nodes connect
(docs/system/execution-nodes.md#the-node-link).

The only door a node dials besides the gateway. It answers a node and
nobody else: the credential an authority signed, admitted before
anything is heard (api/services/nodes/link.py). A browser has no
business here; a person's session is not a credential.
"""

from fastapi import APIRouter, WebSocket

from server.setup.app_state import get_node_links

router = APIRouter()


@router.websocket("/nodes/link")
async def node_link(websocket: WebSocket):
    await get_node_links().serve(websocket)
