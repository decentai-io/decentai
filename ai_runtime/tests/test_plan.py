"""The plan's rules, on their own: ids the runtime assigns, revisions
that keep what is already true, evidence only the trace holds, and
the states an item may move between.
"""

from ai_runtime.reasoning.plan import Plan


class TestIds:
    def test_items_get_ids_in_order_and_old_plans_get_them_on_load(self):
        plan = Plan([{"text": "a"}, {"text": "b", "status": "done"}])
        assert [i["id"] for i in plan.to_steps()] == ["w1", "w2"]
        # A saved plan keeps its ids; a gap is not reused.
        plan = Plan([{"id": "w3", "text": "c"}, {"text": "d"}])
        assert [i["id"] for i in plan.to_steps()] == ["w3", "w4"]

    def test_a_revision_keeps_unchanged_items_whole(self):
        plan = Plan()
        plan.replace(["Save", "Tell"])
        plan.update("w1", "active")
        plan.attach(["stg_1"])
        plan.update("w1", "done")
        plan.replace(["Save", "Tell the user", "Archive"])
        items = plan.to_steps()
        assert [i["id"] for i in items] == ["w1", "w3", "w4"]
        assert items[0]["status"] == "done" and items[0]["evidence"] == ["stg_1"]
        assert items[1]["status"] == "pending"        # reworded: new


class TestRules:
    def test_evidence_must_be_in_the_trace(self):
        plan = Plan([{"text": "a"}])
        ok, why = plan.update("w1", "done", evidence=["stg_x"], proven=set())
        assert not ok and "stg_x" in why
        ok, _ = plan.update("w1", "done", evidence=["stg_x"], proven={"stg_x"})
        assert ok and plan.to_steps()[0]["verified"] is True

    def test_done_without_evidence_stands_but_shows_as_unverified(self):
        plan = Plan([{"text": "Answer the question"}])
        ok, _ = plan.update("w1", "done")
        assert ok
        assert plan.to_steps()[0]["verified"] is False
        assert "UNVERIFIED" in plan.render()

    def test_evidence_lands_only_on_active_items(self):
        plan = Plan([{"text": "a"}, {"text": "b"}])
        plan.update("w2", "active")
        plan.attach(["stg_1", "job_1", "stg_1"])
        a, b = plan.to_steps()
        assert a["evidence"] == [] and b["evidence"] == ["stg_1", "job_1"]

    def test_blocked_needs_a_blocker_and_done_clears_it(self):
        plan = Plan([{"text": "a"}])
        ok, why = plan.update("w1", "blocked")
        assert not ok and "blocker" in why
        ok, _ = plan.update("w1", "blocked", blocker="no access")
        assert ok and plan.blocked()[0].blocker == "no access"
        assert "BLOCKED: no access" in plan.render()
        plan.update("w1", "done")
        assert plan.to_steps()[0]["blocker"] == ""

    def test_active_waits_for_dependencies(self):
        plan = Plan([{"text": "fetch"}, {"text": "post"}])
        ok, _ = plan.update("w2", depends_on=["w1"])
        assert ok
        ok, why = plan.update("w2", "active")
        assert not ok and "waits on w1" in why
        plan.update("w1", "done")
        ok, _ = plan.update("w2", "active")
        assert ok
        ok, why = plan.update("w2", depends_on=["w9"])
        assert not ok and "w9" in why

    def test_items_are_addressed_by_id_or_position(self):
        plan = Plan([{"text": "a"}, {"text": "b"}])
        assert plan.get(2).id == "w2" and plan.get("w2").text == "b"
        assert plan.get(3) is None and plan.get("w3") is None
        ok, why = plan.update("w3", "done")
        assert not ok and "w1, w2" in why

    def test_finished_means_nothing_pending_or_active(self):
        plan = Plan([{"text": "a"}, {"text": "b"}])
        assert not plan.finished()
        plan.update("w1", "done")
        plan.update("w2", "blocked", blocker="x")
        assert plan.finished() and plan.outstanding() == []


class TestAllDone:
    def test_done_everywhere_and_nothing_else(self):
        plan = Plan()
        assert plan.all_done() is False                 # no plan is not a done plan
        plan.replace(["One", "Two"])
        plan.update("w1", "done")
        assert plan.all_done() is False
        plan.update("w2", "done")
        assert plan.all_done() is True

    def test_a_blocked_item_is_not_done(self):
        plan = Plan()
        plan.replace(["One", "Two"])
        plan.update("w1", "done")
        plan.update("w2", "blocked", blocker="waiting on the user")
        assert plan.finished() is True                  # nothing owed
        assert plan.all_done() is False                 # but not over
