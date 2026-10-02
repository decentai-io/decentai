from api.routing.registry.account_registry import build_account_endpoints
from api.routing.registry.agents_registry import build_agents_endpoints
from api.routing.registry.ai_registry import build_ai_endpoints
from api.routing.registry.data_layer_registry import build_data_layer_endpoints
from api.routing.registry.iam_registry import build_iam_endpoints
from api.routing.registry.settings_registry import build_settings_endpoints


def build_endpoints():
    endpoints = {
        "Account": build_account_endpoints(),
        "AI": build_ai_endpoints(),
        "Agents": build_agents_endpoints(),
        "IAM": build_iam_endpoints(),
        "Settings": build_settings_endpoints(),
        **build_data_layer_endpoints(),
    }

    return normalize_endpoints(endpoints)


def normalize_endpoints(endpoints: dict) -> dict:
    return {
        domain.lower(): {
            controller.lower(): handler
            for controller, handler in controllers.items()
        }
        for domain, controllers in endpoints.items()
    }
