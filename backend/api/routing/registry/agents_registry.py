from api.endpoints.app.agents.agent_controller import AgentController
from api.endpoints.app.agents.monitor_controller import MonitorController


def build_agents_endpoints():
    return {
        "Agent": AgentController(),
        # What agents use and did, read from the runtime
        # (docs/system/monitoring.md).
        "Monitor": MonitorController(),
    }
