from server.setup.app_state import get_state, reset_state


def init_state(settings) -> None:
    state = get_state()

    # The guard names the LAST thing built, so a build that failed partway
    # is not mistaken for a finished one. Anything that does raise clears
    # what it managed to build: half-initialized state that survives is
    # worse than none, because every later get_* would fail on a slot
    # nobody can see is missing.
    if state.runtime_clients is not None:
        return

    from database import MongoDB
    from server.authentication.access import AccessController
    from api import APIRouter
    from api.services.chat_session.relay import RuntimeClientManager
    from api.services.cluster import Cluster
    from api.services.nodes.link import NodeLinks
    from server.ws.ws_manager import WSManager

    try:
        state.settings = settings
        state.db = MongoDB(settings)
        state.access_controller = AccessController(settings)
        # This process, as one of the Data Core's replicas: what it holds
        # is written where the others can see it, and they reach it over
        # the bus (docs/system/cluster.md). Before everything that holds
        # a connection, which says where it holds it.
        state.cluster = Cluster()

        # The router is placed in state BEFORE its endpoints are built:
        # endpoint controllers read it (and the db/access_controller) from
        # app_state in their constructors.
        state.api_router = APIRouter()
        state.api_router.initialize_endpoints()

        state.ws_manager = WSManager()
        # The nodes that connect, and the relay that carries chats over
        # their links (docs/system/execution-nodes.md).
        state.node_links = NodeLinks()
        state.runtime_clients = RuntimeClientManager(state.node_links)
    except Exception:
        reset_state()
        raise


async def shutdown_state() -> None:
    """Release what the process holds, then forget it.

    Resources are closed before the references go: the runtime clients own
    live sockets and reader tasks, and dropping them unclosed would leave
    both running. Closing is best-effort — a shutdown that raises would
    leave the rest of the teardown undone.
    """
    state = get_state()

    if state.runtime_clients is not None:
        try:
            await state.runtime_clients.close_all()
        except Exception:
            pass
    if state.node_links is not None:
        try:
            await state.node_links.close_all()
        except Exception:
            pass
    # Last: the replica says it stopped once it holds nothing more.
    if state.cluster is not None:
        try:
            await state.cluster.stop()
        except Exception:
            pass

    reset_state()
