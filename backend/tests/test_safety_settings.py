"""Settings:Safety — what agents may do without asking: the setting and
its checks, what of it the runtime's contract carries, the rule that
settles a code card as it is opened, and where a call connected, on
its line in the audit trail."""

from conftest import app_call
from test_ai_audit import events_for
from test_ai_messages import make_chat
from test_runtime_secret_use import (  # noqa: F401
    runtime_call, runtime_headers, signing_key,
)

DEFAULTS = {"blocked_sites": [], "scripts": "always", "programs": "always",
            "packages": "any", "allowed_packages": [], "mcp": "allowed"}


class TestTheSetting:
    def test_every_row_starts_where_the_platform_stood(self, admin, seed):
        answer = app_call(admin, "Settings:Safety:Get", {}).json()
        assert answer["safety"] == DEFAULTS
        assert answer["choices"] == {
            "scripts": ["always", "once_per_site"],
            "programs": ["always", "corrections", "quiet"],
            "packages": ["any", "listed"],
            "mcp": ["allowed", "blocked"]}

    def test_rows_are_changed_one_at_a_time_and_names_are_tidied(self, admin, seed):
        changed = app_call(admin, "Settings:Safety:Update", {
            "blocked_sites": ["Example.ORG ", "ads.example.net.", "example.org", ""],
            "programs": "corrections"})
        assert changed.status_code == 200, changed.text
        safety = changed.json()["safety"]
        assert safety["blocked_sites"] == ["ads.example.net", "example.org"]
        assert (safety["programs"], safety["scripts"]) == ("corrections", "always")
        listed = app_call(admin, "Settings:Safety:Update", {
            "packages": "listed", "allowed_packages": ["Typing_Extensions", "pandas"]})
        assert listed.json()["safety"]["allowed_packages"] == ["pandas", "typing-extensions"]
        # What was not named stands.
        assert listed.json()["safety"]["blocked_sites"] == ["ads.example.net", "example.org"]

    def test_what_is_not_a_choice_a_site_or_a_package_is_refused(self, admin, seed):
        for changes, said in (
            ({"programs": "never"}, "programs is one of"),
            ({"blocked_sites": ["https://example.org/x"]}, "A site is a name"),
            ({"blocked_sites": ["10.0.0.7"]}, "A site is a name"),
            ({"blocked_sites": ["*.example.org"]}, "A site is a name"),
            ({"blocked_sites": "example.org"}, "A list is expected"),
            ({"allowed_packages": ["pandas==2.2"]}, "A package is named"),
            ({"allowed_packages": ["p"] * 301}, "at most 300"),
        ):
            refused = app_call(admin, "Settings:Safety:Update", changes)
            assert refused.status_code == 400 and said in refused.text, changes
        assert app_call(admin, "Settings:Safety:Get", {}).json()["safety"] == DEFAULTS

    def test_a_change_is_on_the_trail_with_what_it_was(self, admin, seed):
        app_call(admin, "Settings:Safety:Update", {"scripts": "once_per_site"})
        app_call(admin, "Settings:Safety:Update", {"scripts": "once_per_site"})
        from database.stores import AuditStore

        events = list(AuditStore().col.find({"event_type": "settings.safety"}))
        assert len(events) == 1, "saying the same again changes nothing"
        assert events[0]["details"] == {
            "scripts": {"from": "always", "to": "once_per_site"}}
        assert events[0]["actor"] == "admin@test.org"


class TestTheContractCarriesWhatTheRuntimeEnforces:
    def test_blocked_sites_and_the_package_list_and_not_the_asking(
            self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        safety = runtime_call(anon, seed, chat_id, "AI:Chat:Contract", {}).json()["data"]["safety"]
        assert safety == {"blocked_sites": [], "packages": "any", "allowed_packages": []}
        app_call(admin, "Settings:Safety:Update", {
            "blocked_sites": ["example.org"], "packages": "listed",
            "allowed_packages": ["pandas"], "programs": "quiet"})
        safety = runtime_call(anon, seed, chat_id, "AI:Chat:Contract", {}).json()["data"]["safety"]
        assert safety == {"blocked_sites": ["example.org"], "packages": "listed",
                          "allowed_packages": ["pandas"]}


class TestACardTheSettingSettles:
    """How often a person is asked is decided where the cards are. A
    looser choice applies only to code the assistant found to do what
    it says, and only a person's own yes counts as having allowed."""

    PROGRAM = {"language": "python", "code": "print(1)", "purpose": "Prints one.",
               "packages": ["pandas"], "hosts": ["api.example.com"],
               "review": {"verdict": "agrees", "note": "It prints one."}}
    SCRIPT = {"language": "javascript", "code": "return 1", "purpose": "Counts.",
              "where": "shop.example.com",
              "review": {"verdict": "agrees", "note": "It counts."}}

    @staticmethod
    def propose(anon, seed, chat_id, code, call_id="c_1", agent="agt_code"):
        opened = runtime_call(anon, seed, chat_id, "AI:Approval:Open", {
            "request": {"kind": "question", "function": f"{agent}.program.run",
                        "agent_id": agent, "agent_name": "Code",
                        "question": "Code wants to run code", "choices": [],
                        "expects": "code", "code": code, "call_id": call_id}})
        assert opened.status_code == 200, opened.text
        return opened.json()["data"]

    def allowed_by_the_person(self, anon, admin, seed, chat_id, code, **where):
        opened = self.propose(anon, seed, chat_id, code, **where)
        assert "settled" not in opened
        answered = app_call(admin, "AI:Approval:Decide", {
            "approval_id": opened["approval_id"], "answer": "allow"})
        assert answered.status_code == 200, answered.text
        return opened["approval_id"]

    def test_by_default_every_program_and_script_is_a_card(
            self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        self.allowed_by_the_person(anon, admin, seed, chat_id, self.PROGRAM)
        assert "settled" not in self.propose(anon, seed, chat_id, self.PROGRAM)
        self.allowed_by_the_person(anon, admin, seed, chat_id, self.SCRIPT, call_id="c_2")
        assert "settled" not in self.propose(anon, seed, chat_id, self.SCRIPT, call_id="c_3")

    def test_a_correction_that_needs_nothing_new_is_not_asked_again(
            self, anon, admin, seed, signing_key):
        app_call(admin, "Settings:Safety:Update", {"programs": "corrections"})
        chat_id = make_chat(admin)
        # The first program of a call is always the person's to allow.
        self.allowed_by_the_person(anon, admin, seed, chat_id, self.PROGRAM)
        corrected = {**self.PROGRAM, "code": "print(2)", "packages": []}
        settled = self.propose(anon, seed, chat_id, corrected)
        assert settled["settled"] == "allow"
        from database.stores import ApprovalStore

        record = ApprovalStore().in_chat(settled["approval_id"], chat_id)
        assert (record["status"], record["answer"], record["resolved_by"]) == (
            "answered", "allow", "setting")
        assert record["answer_label"] == "Allowed by the Safety setting"
        # Nothing waits on the person, and the trail says who decided.
        assert app_call(admin, "AI:Approval:List", {"chat_id": chat_id}).json()["data"]["approvals"] == []
        resolved = [e for e in events_for(admin, chat_id)
                    if e["event_type"] == "approval.resolved"
                    and e["details"].get("decision") == "setting"]
        assert len(resolved) == 1

    def test_a_correction_that_needs_more_or_is_another_calls_is_a_card(
            self, anon, admin, seed, signing_key):
        app_call(admin, "Settings:Safety:Update", {"programs": "corrections"})
        chat_id = make_chat(admin)
        self.allowed_by_the_person(anon, admin, seed, chat_id, self.PROGRAM)
        for changed in ({"hosts": ["api.example.com", "files.example.com"]},
                        {"packages": ["pandas", "numpy"]},
                        {"credentials": ["SHOP_TOKEN for api.example.com"]},
                        {"files": ["notes.txt"]}):
            assert "settled" not in self.propose(
                anon, seed, chat_id, {**self.PROGRAM, **changed}), changed
        assert "settled" not in self.propose(
            anon, seed, chat_id, self.PROGRAM, call_id="c_other")
        assert "settled" not in self.propose(
            anon, seed, chat_id, self.PROGRAM, agent="agt_other")

    def test_code_the_review_doubts_is_always_a_card(
            self, anon, admin, seed, signing_key):
        app_call(admin, "Settings:Safety:Update", {
            "programs": "quiet", "scripts": "once_per_site"})
        chat_id = make_chat(admin)
        quiet = {**self.PROGRAM, "packages": [], "hosts": []}
        for review in ({"verdict": "differs", "note": "It sends the file away."},
                       {"verdict": "unread", "note": ""}, None):
            doubted = {key: value for key, value in quiet.items() if key != "review"}
            if review is not None:
                doubted["review"] = review
            assert "settled" not in self.propose(anon, seed, chat_id, doubted), review

    def test_a_program_that_reaches_nothing_is_not_asked_where_that_was_chosen(
            self, anon, admin, seed, signing_key):
        app_call(admin, "Settings:Safety:Update", {"programs": "quiet"})
        chat_id = make_chat(admin)
        quiet = {**self.PROGRAM, "packages": ["pandas"], "hosts": []}
        assert self.propose(anon, seed, chat_id, quiet)["settled"] == "allow"
        # One that reaches a site, or uses a credential, is the person's.
        assert "settled" not in self.propose(anon, seed, chat_id, self.PROGRAM)
        assert "settled" not in self.propose(anon, seed, chat_id, {
            **quiet, "credentials": ["SHOP_TOKEN for api.example.com"]})

    def test_a_script_is_asked_once_for_a_site_in_a_chat(
            self, anon, admin, seed, signing_key):
        app_call(admin, "Settings:Safety:Update", {"scripts": "once_per_site"})
        chat_id = make_chat(admin)
        assert "settled" not in self.propose(anon, seed, chat_id, self.SCRIPT)
        self.allowed_by_the_person(anon, admin, seed, chat_id, self.SCRIPT, call_id="c_2")
        again = {**self.SCRIPT, "code": "return 2"}
        assert self.propose(anon, seed, chat_id, again, call_id="c_3")["settled"] == "allow"
        # Another site, and another chat, are asked.
        assert "settled" not in self.propose(
            anon, seed, chat_id, {**again, "where": "bank.example.com"}, call_id="c_4")
        other = make_chat(admin, request_id="c2")
        assert "settled" not in self.propose(anon, seed, other, again)

    def test_what_the_setting_let_through_is_no_persons_word(
            self, anon, admin, seed, signing_key):
        """A quiet program settled by the setting does not make the next
        one a correction of something a person allowed."""
        app_call(admin, "Settings:Safety:Update", {"programs": "quiet"})
        chat_id = make_chat(admin)
        quiet = {**self.PROGRAM, "packages": [], "hosts": []}
        assert self.propose(anon, seed, chat_id, quiet)["settled"] == "allow"
        app_call(admin, "Settings:Safety:Update", {"programs": "corrections"})
        assert "settled" not in self.propose(anon, seed, chat_id, quiet)

    def test_a_person_who_declined_allowed_nothing(
            self, anon, admin, seed, signing_key):
        app_call(admin, "Settings:Safety:Update", {"programs": "corrections"})
        chat_id = make_chat(admin)
        opened = self.propose(anon, seed, chat_id, self.PROGRAM)
        app_call(admin, "AI:Approval:Decide", {
            "approval_id": opened["approval_id"], "answer": "deny"})
        assert "settled" not in self.propose(anon, seed, chat_id, self.PROGRAM)


class TestWhereACallConnected:
    EXECUTION = {"event_type": "execution", "agent_id": "agt_1", "agent_name": "Browser",
                 "function": "browser.browse.run", "permission_level": 2,
                 "chat_level": 2, "status": "success", "duration_ms": 900,
                 "inputs": {"goal": "Read the page"}}

    def test_the_hosts_are_kept_on_the_calls_line(self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        recorded = runtime_call(anon, seed, chat_id, "AI:Audit:Record", {"event": {
            **self.EXECUTION,
            "reached": [{"host": "shop.example.com", "connections": 14},
                        {"host": "cdn.example.net", "connections": 3}],
            "reached_more": 22}})
        assert recorded.status_code == 200, recorded.text
        details = events_for(admin, chat_id)[0]["details"]
        assert details["reached"] == [
            {"host": "shop.example.com", "connections": 14},
            {"host": "cdn.example.net", "connections": 3}]
        assert details["reached_more"] == 22

    def test_what_is_not_a_host_and_a_count_is_left_out(self, anon, admin, seed, signing_key):
        chat_id = make_chat(admin)
        runtime_call(anon, seed, chat_id, "AI:Audit:Record", {"event": {
            **self.EXECUTION,
            "reached": [{"host": "", "connections": 1}, "shop.example.com",
                        {"host": "shop.example.com", "connections": "many"},
                        *({"host": f"h{n}.example.com", "connections": 1}
                          for n in range(30))],
            "reached_more": "lots"}})
        details = events_for(admin, chat_id)[0]["details"]
        assert [r["host"] for r in details["reached"]][:2] == [
            "h0.example.com", "h1.example.com"]
        assert len(details["reached"]) == 17 and "reached_more" not in details
