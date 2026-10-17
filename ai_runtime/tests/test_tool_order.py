"""The tools an opened agent brings are offered in the order the agents
were opened, which using one does not change: the tools are the front
of every request a provider caches, so opening an agent adds to the end
of the list and touching one moves nothing (docs/system/assistant.md,
`close_agent`). Past the cap, the most recently used agents' functions
are still the ones that fit.
"""

import json
from types import SimpleNamespace

from ai_runtime.mind.actions import FunctionTools
from ai_runtime.mind.state import AssistantState


def stub_agent(agent_id, *functions):
    manifest = SimpleNamespace(functions=lambda: [
        (f"{agent_id}.{name}", None, {"description": name})
        for name in functions])
    return SimpleNamespace(manifest=manifest, granted=lambda declared: declared)


class TestTheTools:
    AGENTS = {"a": stub_agent("a", "one", "two"),
              "b": stub_agent("b", "three"),
              "c": stub_agent("c", "four")}

    @staticmethod
    def names(tools):
        return [t["function"]["name"] for t in tools.tools]

    def test_a_newly_opened_agents_tools_come_last(self):
        state = AssistantState()
        state.open_agent("a")
        before = self.names(FunctionTools(self.AGENTS, state.opened, 1,
                                          order=state.opened_in_order()))
        state.open_agent("b")
        after = self.names(FunctionTools(self.AGENTS, state.opened, 1,
                                         order=state.opened_in_order()))
        assert after[:len(before)] == before
        assert after == ["a__one", "a__two", "b__three"]

    def test_using_an_agent_again_does_not_reorder_them(self):
        state = AssistantState()
        for agent_id in ("a", "b", "c"):
            state.open_agent(agent_id)
        state.touch_agent("a")                  # opened again: most recent
        assert state.opened == ["b", "c", "a"]
        assert self.names(FunctionTools(self.AGENTS, state.opened, 1,
                                        order=state.opened_in_order())) \
            == ["a__one", "a__two", "b__three", "c__four"]

    def test_past_the_cap_the_most_recent_are_kept_and_offered_in_order(self, monkeypatch):
        monkeypatch.setattr(FunctionTools, "MAX_FUNCTIONS", 2)
        state = AssistantState()
        for agent_id in ("a", "b", "c"):
            state.open_agent(agent_id)
        tools = FunctionTools(self.AGENTS, state.opened, 1,
                              order=state.opened_in_order())
        # c and b were opened last, so theirs are the ones that fit...
        assert self.names(tools) == ["b__three", "c__four"]
        # ...and a's are still callable by name, and said to be.
        assert tools.omitted == ["a.one", "a.two"]

    def test_the_order_is_kept_with_the_state(self):
        state = AssistantState()
        for agent_id in ("a", "b"):
            state.open_agent(agent_id)
        state.touch_agent("a")
        again = AssistantState.from_dict(json.loads(json.dumps(state.to_dict())))
        assert again.opened == ["b", "a"]
        assert again.opened_in_order() == ["a", "b"]
        # A state saved before the order was kept reads its open list.
        older = state.to_dict()
        del older["opened_at"]
        assert AssistantState.from_dict(older).opened_in_order() == ["b", "a"]

    def test_closed_and_opened_again_goes_to_the_end(self):
        state = AssistantState()
        for agent_id in ("a", "b"):
            state.open_agent(agent_id)
        state.close_agent("a")
        state.open_agent("a")
        assert state.opened_in_order() == ["b", "a"]
