from api.endpoints.app.agents.agent_controller import AgentController


def build_agents_endpoints():
    return {
        "Agent": AgentController(),
    }
