"""The one gate: rules, then the level against the run's trust, then an answer remembered or given once, else the
person, asked for one step's calls in one card (gate.py; runtime.md §5)."""

import asyncio
from types import SimpleNamespace

import pytest

from runtime.controllers.gate import Gate


def item(id, function="report", **more):
    return {"id": id, "agent": "probe", "function": function, "record": "", **more}


def asking(answer=True):
    """An ask that keeps what it was shown, and answers every item, or as given."""
    shown = []

    async def ask(items):
        shown.append([dict(i) for i in items])
        return answer if isinstance(answer, dict) else {i["id"]: answer for i in items}

    return ask, shown


async def test_the_rules_judge_first_then_the_level_against_the_runs_trust():
    ask, shown = asking()
    gate = Gate(ask, rules=[{"effect": "allow", "actions": ["agent:*"]},
                            {"effect": "deny", "actions": ["agent:probe:wipe"]}])
    await gate.check(1, item("c1"), 1, "agent:probe:report")
    await gate.check(3, item("c2"), 3, "agent:probe:report")
    assert shown == []
    with pytest.raises(PermissionError, match="policy"):
        await gate.check(0, item("c3", "wipe"), 3, "agent:probe:wipe")
    call = SimpleNamespace(deciding=0)
    await gate.check(2, item("c4"), 1, "agent:probe:report", call)
    assert [i["id"] for i in shown[0]] == ["c4"] and call.deciding == 0


async def test_one_steps_calls_are_one_card_the_same_ones_merged_each_answered():
    ask, shown = asking({"c1": True, "c3": False})
    gate = Gate(ask)
    results = await asyncio.gather(gate.check(2, item("c1", inputs="a"), 1), gate.check(2, item("c2", inputs="a"), 1),
                                   gate.check(2, item("c3", inputs="b"), 1), return_exceptions=True)
    assert [[i["id"] for i in card] for card in shown] == [["c1", "c3"]]
    assert results[:2] == [None, None] and isinstance(results[2], PermissionError)


async def test_a_remembered_answer_holds_and_one_given_once_passes_once():
    ask, shown = asking(False)
    gate = Gate(ask, remembered=[{"agent": "probe", "function": "wipe", "record": "", "allowed": False}])
    with pytest.raises(PermissionError, match="remembered"):
        await gate.check(2, item("c1", "wipe"), 1)
    gate.granted.append(("probe", "report", ""))
    await gate.check(2, item("c2"), 1)
    with pytest.raises(PermissionError, match="declined"):
        await gate.check(2, item("c3"), 1)
    assert len(shown) == 1


async def test_items_that_waited_on_a_card_are_asked_on_it_apart_from_new_ones():
    ask, shown = asking()
    gate = Gate(ask)
    await asyncio.gather(gate.check(2, item("c1", card="card7"), 1), gate.check(2, item("c2"), 1))
    assert sorted([[(i["id"], i.get("card")) for i in card] for card in shown]) == [[("c1", "card7")], [("c2", None)]]
