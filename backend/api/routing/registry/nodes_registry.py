from api.endpoints.app.nodes.authority_controller import AuthorityController
from api.endpoints.app.nodes.node_controller import NodeController


def build_nodes_endpoints():
    return {
        # Who may vouch for an execution node, and the nodes that were
        # admitted (docs/system/execution-nodes.md).
        "Authority": AuthorityController(),
        "Node": NodeController(),
    }
