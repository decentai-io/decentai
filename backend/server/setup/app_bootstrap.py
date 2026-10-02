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
    from server.ws.ws_manager import WSManager

    try:
        state.settings = settings
        state.db = MongoDB(settings)
        state.access_controller = AccessController(settings)

        # The router is placed in state BEFORE its endpoints are built:
        # endpoint controllers read it (and the db/access_controller) from
        # app_state in their constructors.
        state.api_router = APIRouter()
        state.api_router.initialize_endpoints()

        state.ws_manager = WSManager()
        state.runtime_clients = RuntimeClientManager()
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

    reset_state()
