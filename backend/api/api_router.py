import traceback
import inspect
import asyncio

from api.routing.endpoint_registry import build_endpoints

from server.authentication.policy import ActionCatalog
from server.custom_logging import CustomLoggerFactory
from server.setup.app_state import get_access_controller


class APIRouter:
    """Dispatches Domain:Controller:action requests to endpoint handlers.

    Authentication happens in the routes (they hand this a verified user or
    nothing); authorization happens HERE, once, for every endpoint — the
    same AccessController answers both questions, and the action checked is
    the endpoint itself. Controllers contain no authorization code.
    """

    def __init__(self):
        self.endpoints = None

        self.logger = CustomLoggerFactory.get_logger(__name__)

    # Calls that only retrieve or inspect state. Every other gateway action
    # is a change (or an attempted change) and belongs in the audit trail.
    # AI actions are not here: they keep their own witnesses (below).
    READ_ONLY_ACTIONS = {
        "list", "get", "peers", "available", "leaving", "shapes",
        "grants", "secretgrants", "secretlendable", "sources", "samples",
        "instances", "resolve", "download",
        "fetch_package", "pinned_digests",
    }

    # These successful operations already append a richer, domain-specific
    # event at their transaction boundary. The gateway still records their
    # failures, but does not add a duplicate success row.
    DETAILED_AUDIT_ENDPOINTS = {
        "secrets:secret:use", "settings:llm:use",
        # Every agents action is dispatched as agents:agent:<action> —
        # the router keys this set by the normalized endpoint, so the
        # names here must be the ones it actually sees.
        "agents:agent:install", "agents:agent:delete",
        "agents:agent:grant", "agents:agent:revoke",
        "agents:agent:secretgrant", "agents:agent:secretrevoke",
        "agents:agent:secretlendmany",
        "agents:agent:sourcecreate", "agents:agent:sourcerefresh",
        "agents:agent:sourceupdate", "agents:agent:sourcepurge",
        "agents:agent:sourcetransfer", "agents:agent:sourcedelete",
        "agents:agent:loadsamples", "agents:agent:removesamples",
    }

    @classmethod
    def _should_audit(cls, endpoint, action):
        # AI has its own execution, approval, schedule, and credential
        # witnesses at the actual transaction boundaries. This gateway
        # fills the former gap for the rest of the platform.
        return not endpoint.startswith("ai:") and action not in cls.READ_ONLY_ACTIONS

    def _audit_action(self, endpoint, action, user, status_code, msg=None):
        if not self._should_audit(endpoint, action):
            return
        if status_code == 200 and endpoint in self.DETAILED_AUDIT_ENDPOINTS:
            return
        from database.stores import AuditStore
        outcome = "success" if status_code == 200 else (
            "denied" if status_code in (401, 403) else "failed"
        )
        details = {"endpoint": endpoint, "outcome": outcome, "status": status_code}
        if status_code != 200:
            details["reason"] = self._reason(msg)
        AuditStore().append(
            "platform.action", user, function=endpoint, details=details,
        )

    @staticmethod
    def _reason(msg, limit=300):
        """The human-readable reason in a failed result — an `error`
        (a sentence, or a {code, message} pair) or the first message
        description — bounded for a log line."""
        reason = ""
        if isinstance(msg, dict):
            error = msg.get("error")
            if isinstance(error, dict):
                reason = str(error.get("message") or error.get("code") or error)
            elif error:
                reason = str(error)
            else:
                messages = msg.get("messages")
                if isinstance(messages, list) and messages:
                    first = messages[0]
                    reason = str(first.get("description") if isinstance(first, dict) else first)
        reason = " ".join(reason.split())
        return reason[:limit] if reason else "no reason given"

    @staticmethod
    def prepare_method_failed_response(label, message, status_code=400):
        return (
            {
                "title": label,
                "type": "error",
                "messages": [{"description": message}],
            },
            status_code,
        )

    def initialize_endpoints(self):
        if self.endpoints is not None:
            self.logger.warning("Endpoints already initialized, skipping")
            return

        self.endpoints = build_endpoints()
        self.logger.info("API endpoints initialized successfully")

    async def route(self, data: dict, user: dict):
        endpoint = data.get("endpoint", "")

        if not endpoint:
            self.logger.error(f"User {user.get('email')} failed to provide an endpoint")
            return self.prepare_method_failed_response("API Error", "API Endpoint is missing")

        try:
            domain, controller, action = endpoint.split(":")
        except ValueError:
            return self.prepare_method_failed_response(
                "API Error",
                f"Invalid endpoint format: {endpoint}"
            )

        domain = domain.strip().lower()
        controller = controller.strip().lower()
        action = action.strip().lower()

        # Only public actions are dispatchable. Rejecting underscore-prefixed
        # names keeps helpers (_guard, _payload) and dunders (__init__) off the
        # callable surface, so the getattr below can never reach them.
        if not action or action.startswith("_"):
            return self.prepare_method_failed_response("API Error", "Unknown endpoint")

        normalized_endpoint = f"{domain}:{controller}:{action}"

        if not get_access_controller().is_allowed(user, normalized_endpoint):
            result = self.prepare_method_failed_response(
                "Permission Error",
                "You are not allowed to perform this action.",
                403,
            )
            self._audit_action(normalized_endpoint, action, user, result[1], result[0])
            return result

        # Only what the catalog names is an endpoint. A controller's
        # other public methods — a helper another controller calls, one
        # it inherited — are found by getattr exactly like an action,
        # and a wildcard policy would allow them.
        if not ActionCatalog.is_known(normalized_endpoint):
            return self.prepare_method_failed_response("API Error", "Unknown endpoint")

        if domain not in self.endpoints:
            return self.prepare_method_failed_response(
                "API Error",
                f"Domain {domain} not found"
            )

        if controller not in self.endpoints[domain]:
            return self.prepare_method_failed_response(
                "API Error",
                f"Controller {controller} not found in {domain}"
            )

        handler = self.endpoints[domain][controller]
        function = getattr(handler, action, None)

        if not callable(function):
            return self.prepare_method_failed_response(
                "API Error",
                f"Action {action} not callable"
            )

        try:
            if inspect.iscoroutinefunction(function):
                result = await function(data, user)
            else:
                result = await asyncio.to_thread(function, data, user)

            if not (isinstance(result, tuple) and len(result) == 2):
                return self.prepare_method_failed_response(
                    "Server Error",
                    f"Endpoint {normalized_endpoint} returned invalid response type "
                    f"(expected (msg, status_code))",
                    500,
                )

            msg, status_code = result

            self._audit_action(normalized_endpoint, action, user, status_code, msg)

            if status_code == 200:
                self.logger.info(
                    f"{user.get('email')} called {normalized_endpoint} successfully"
                )
            else:
                # The reason travels with the status: a transient fetch
                # failure or a refused field is otherwise only on the
                # caller's screen, and gone when they ask about it.
                self.logger.warning(
                    f"{user.get('email')} call to {normalized_endpoint} "
                    f"returned {status_code}: {self._reason(msg)}"
                )

            return msg, status_code

        except Exception as e:
            self.logger.error(traceback.format_exc())
            self.logger.error(f"Error processing {normalized_endpoint}: {e}")
            result = self.prepare_method_failed_response(
                "Server Error",
                f"Internal error while processing {normalized_endpoint}",
                500,
            )
            self._audit_action(normalized_endpoint, action, user, result[1], result[0])
            return result
