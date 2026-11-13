"""Rules with scopes: a statement narrowed to some values holds a call to them (documentation/api-server.md §3)."""

from shared.rules import ALLOW, ASK, DENY, evaluate, normalizations, scope_values

MANIFEST = {"authorization": {"scopes": {"notebook": {"description": "A notebook.", "normalization": "lowercase"},
                                         "project": {"description": "A project."}}},
            "functions": [{"id": "save", "authorization": {"notebook": {"from_input": "notebook"}}},
                          {"id": "list"}]}
SAVE, LIST = MANIFEST["functions"]
NORMS = normalizations(MANIFEST)


def values(inputs, function=SAVE):
    return scope_values(MANIFEST, function, inputs)


class TestScopeValues:
    def test_each_scope_the_function_works_along_carries_its_inputs_value_normalized(self):
        assert values({"notebook": "Work"}) == {"notebook": "work"}
        assert values({}) == {"notebook": None} and values({"notebook": ""}) == {"notebook": None}
        assert values({"notebook": "Work"}, LIST) == {}
        assert NORMS == {"notebook": "lowercase"}


class TestScopedStatements:
    ALLOW_WORK = [{"effect": "allow", "actions": ["agent:notes:*"], "scopes": {"notebook": ["Work"]}}]

    def test_an_allow_holds_to_its_values_compared_as_the_manifest_says(self):
        assert evaluate(self.ALLOW_WORK, "agent:notes:save", values({"notebook": "WORK"}), NORMS) == ALLOW
        assert evaluate(self.ALLOW_WORK, "agent:notes:save", values({"notebook": "home"}), NORMS) == DENY

    def test_a_call_that_gives_no_value_is_not_within_it(self):
        assert evaluate(self.ALLOW_WORK, "agent:notes:save", values({}), NORMS) == DENY

    def test_a_scope_the_function_does_not_work_along_does_not_narrow_it(self):
        assert evaluate(self.ALLOW_WORK, "agent:notes:list", values({}, LIST), NORMS) == ALLOW

    def test_every_scope_of_a_statement_must_hold(self):
        rules = [{"effect": "ask", "actions": ["agent:notes:save"], "scopes": {"notebook": ["work"], "project": ["x"]}}]
        assert evaluate(rules, "agent:notes:save", values({"notebook": "work"}), NORMS) == ASK

    def test_a_scoped_deny_refuses_its_values_alone(self):
        rules = [{"effect": "allow", "actions": ["agent:notes:*"]},
                 {"effect": "deny", "actions": ["agent:notes:save"], "scopes": {"notebook": ["home"]}}]
        assert evaluate(rules, "agent:notes:save", values({"notebook": "Home"}), NORMS) == DENY
        assert evaluate(rules, "agent:notes:save", values({"notebook": "work"}), NORMS) == ALLOW

    def test_knowing_no_call_a_scoped_allow_may_apply_and_a_scoped_deny_waits_for_the_call(self):
        rules = [{"effect": "allow", "actions": ["agent:notes:*"], "scopes": {"notebook": ["work"]}},
                 {"effect": "deny", "actions": ["agent:other:*"], "scopes": {"notebook": ["home"]}}]
        assert evaluate(rules, "agent:notes:save") == ALLOW
        assert evaluate(rules + [{"effect": "allow", "actions": ["agent:other:*"]}], "agent:other:save") == ALLOW

    def test_statements_without_scopes_are_as_they_were(self):
        rules = [{"effect": "allow", "actions": ["agent:notes:*"]}]
        assert evaluate(rules, "agent:notes:save", values({"notebook": "any"}), NORMS) == ALLOW
        assert evaluate(rules, "iam:user:list") == DENY
