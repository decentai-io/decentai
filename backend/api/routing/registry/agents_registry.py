from api.endpoints.app.agents.agent_controller import AgentController
from api.endpoints.app.agents.monitor_controller import MonitorController
from api.endpoints.app.agents.package_controller import PackageController


def build_agents_endpoints():
    return {
        "Agent": AgentController(),
        # What agents use and did, read from the runtime
        # (docs/system/monitoring.md).
        "Monitor": MonitorController(),
        # The package door: what a runtime asks as itself, for the
        # whole deployment (docs/system/agent-code.md).
        "Package": PackageController(),
    }
