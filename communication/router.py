"""Which controller answers a call: ``{"endpoint": "Domain:Controller:action", "data": {...}}``
(documentation/communication.md)."""

import asyncio
import inspect
import logging
from typing import Any, Callable

log = logging.getLogger("communication")

Answer = tuple[Any, int]


class Router:
    def __init__(self, endpoints: dict[str, dict[str, Any]],
                 allowed: Callable[[dict[str, Any] | None, str], bool] | None = None):
        self.endpoints = {domain.lower(): {name.lower(): controller for name, controller in controllers.items()}
                          for domain, controllers in endpoints.items()}
        self.allowed = allowed

    async def route(self, call: Any, peer: dict[str, Any] | None) -> Answer:
        endpoint = call.get("endpoint") if isinstance(call, dict) else None
        if not isinstance(endpoint, str) or not endpoint:
            return self.failed("API Error", "API Endpoint is missing")
        try:
            domain, controller, action = (part.strip().lower() for part in endpoint.split(":"))
        except ValueError:
            return self.failed("API Error", f"Invalid endpoint format: {endpoint}")
        name = f"{domain}:{controller}:{action}"
        # Only public actions: helpers (_guard) and dunders (__init__) are never reachable.
        if not action or action.startswith("_"):
            return self.failed("API Error", "Unknown endpoint", 404)
        if not (self.allowed(peer, name) if self.allowed is not None else peer is not None):
            return self.failed("Permission Error", "You are not allowed to perform this action.", 403)
        function = getattr(self.endpoints.get(domain, {}).get(controller), action, None)
        if not callable(function):
            return self.failed("API Error", "Unknown endpoint", 404)

        data = call.get("data")
        data = data if isinstance(data, dict) else {}
        try:
            if inspect.iscoroutinefunction(function):
                answer = await function(data, peer)
            else:
                answer = await asyncio.to_thread(function, data, peer)
        except Exception:
            log.exception("%s, called by %s, failed", name, peer["id"] if peer else "a caller with no certificate")
            return self.failed("Server Error", f"Internal error while processing {name}", 500)
        if not (isinstance(answer, tuple) and len(answer) == 2 and isinstance(answer[1], int)):
            return self.failed("Server Error", f"Endpoint {name} returned no (answer, status)", 500)
        return answer

    @staticmethod
    def failed(title: str, message: str, status: int = 400) -> Answer:
        return {"title": title, "type": "error", "messages": [{"description": message}]}, status
