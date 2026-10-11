"""One running call (execution/call.py): a worker's line is answered by
the method of the same name, as the wire shapes it; the names a code
card may carry are checked before the card is shown; an allowed card
opens its hosts on the worker's way out and makes its packages
installable; and the call's end closes what it opened. What each ask
checks for shape is proven where the asks are made (test_agent_executor,
test_agent_llm); this is the object itself."""

from types import SimpleNamespace

import pytest

from ai_runtime.agents.workers import WorkerError
from ai_runtime.execution.call import Call
from ai_runtime.sinks import ChatSinks
from ai_runtime.tests import fake_container


def run(awaitable):
    return fake_container.run(awaitable)


class Place:
    """Stands for a confined worker's place: remembers what it lent."""

    def __init__(self):
        self.open = []

    def lend(self, hosts):
        lent = [(host, None) for host in hosts]
        self.open.extend(lent)
        return lent

    def learn(self, secret_id, credential):
        return self.lend([credential["host"]])

    def take_back(self, lent):
        for one in lent:
            self.open.remove(one)


def placed(place, **given):
    call = Call("c_1", **given)
    call.handle = SimpleNamespace(place=place)
    return call


class TestTheWire:
    def test_each_line_is_answered_by_its_method_in_the_wires_shape(self):
        heard = []

        async def post(text, source, parts):
            heard.append((text, source))
            return True

        async def list_data(resource_id, filters):
            return [{"title": resource_id}]

        async def delete_data(resource_id, ref):
            return True

        agent = SimpleNamespace(agent_id="agt_1", manifest=SimpleNamespace(name="Notes"))
        call = Call("c_1", agent, {}, "notes.note.find", job_id="job_1",
                    resources=SimpleNamespace(list_data=list_data, delete_data=delete_data),
                    sinks=ChatSinks(post=post))

        async def scenario():
            posted = await call.answer("post", {"call_id": "c_1", "text": "Done.",
                                                "displays": []})
            rows = await call.answer("resources.list_data",
                                     {"call_id": "c_1", "resource_id": "note"})
            deleted = await call.answer("resources.delete_data",
                                        {"call_id": "c_1", "resource_id": "note",
                                         "ref": "r_1"})
            return posted, rows, deleted

        posted, rows, deleted = run(scenario())
        assert posted == {"posted": True}
        assert rows == [{"title": "note"}] and deleted == {"deleted": True}
        assert heard == [("Done.", {
            "kind": "agent", "agent": "agt_1", "agent_name": "Notes",
            "function": "notes.note.find", "call_id": "c_1", "job_id": "job_1"})]

    def test_a_line_nobody_speaks_is_refused(self):
        call = Call("c_1", resources=SimpleNamespace())
        for method in ("resources.drop_table", "shout", "resources"):
            with pytest.raises(WorkerError, match="unknown ask"):
                run(call.answer(method, {"call_id": "c_1"}))

    def test_what_the_manifest_did_not_declare_is_not_there(self):
        async def serve(messages, max_tokens=None, pictures=None):
            return "said"

        async def credential(*args):
            return {"user": "u"}

        sinks = ChatSinks(llm=serve, credential=credential)
        plain = Call("c_1", None, {}, sinks=sinks)
        with pytest.raises(WorkerError, match="llm: true"):
            run(plain.llm([{"role": "user", "content": "hi"}]))
        with pytest.raises(WorkerError, match="credentials: true"):
            run(plain.credential("example.com", [{"name": "user"}]))
        with pytest.raises(WorkerError, match="code: true"):
            run(plain.install(["pandas"]))
        declared = Call("c_1", None, {"llm": True, "credentials": True}, sinks=sinks)
        assert run(declared.llm([])) == {"text": "said", "stop_reason": ""}
        assert run(declared.credential("example.com", [{"name": "user"}])) == {"user": "u"}

    def test_nobody_to_ask_is_no_answer_and_no_post(self):
        call = Call("c_1")
        assert run(call.answer("ask", {"call_id": "c_1", "question": "Which?",
                                       "choices": []})) == {"answer": None}
        assert run(call.answer("propose", {"call_id": "c_1", "code": {}})) == {"allowed": None}
        assert run(call.answer("post", {"call_id": "c_1", "text": "x",
                                        "displays": []})) == {"posted": False}
        assert run(call.answer("show", {"call_id": "c_1", "kind": "table"})) == {
            "display_id": None}

    def test_the_clock_stops_while_a_person_decides(self):
        async def ask(question, choices, source, expects=""):
            assert call.waiting == 1
            return "Work"

        call = Call("c_1", sinks=ChatSinks(ask=ask))
        assert run(call.ask("Which?", ["Work", "Home"])) == "Work"
        assert call.waiting == 0 and call.waited >= 0

    def test_a_credential_opens_the_host_it_names_until_the_call_ends(self):
        async def use_secret(resource_id, ref=None):
            return {"host": "mail.sara.example"}

        place = Place()
        call = placed(place, resources=SimpleNamespace(use_secret=use_secret))
        run(call.answer("resources.use_secret", {"call_id": "c_1",
                                                 "resource_id": "mail"}))
        assert place.open == [("mail.sara.example", None)]
        assert call.learned == [("mail.sara.example", None)]
        call.close()
        assert place.open == [] and call.learned == []


class TestWhatACardMayName:
    def test_a_host_is_one_name_and_perhaps_a_port(self):
        assert Call.host("API.Example.com") == "api.example.com"
        assert Call.host("db.example.com:5432") == "db.example.com:5432"
        for written in ("*.example.com", "10.0.0.7", "[::1]", "localhost",
                        "example.com:0", "example.com:70000",
                        "https://example.com", "example.com/path", ""):
            assert Call.host(written) == "", written

    def test_a_proposal_naming_what_cannot_be_allowed_says_which(self):
        assert Call.code_problem({"hosts": ["api.example.com"],
                                  "packages": ["pandas", "requests==2.32.3"]}) == ""
        assert "'*.example.com' is not a host" in Call.code_problem(
            {"hosts": ["api.example.com", "*.example.com"]})
        assert "'git+https://example.com/x' is not a package" in Call.code_problem(
            {"packages": ["git+https://example.com/x"]})


class TestAListOfPackages:
    """Where the deployment keeps a list (Settings:Safety), a program
    may install what is on it and nothing else."""

    def test_a_package_off_the_list_is_refused_before_a_card(self):
        listed = ["pandas", "typing-extensions"]
        assert Call.code_problem(
            {"packages": ["Pandas==2.2", "typing_extensions>=4"]}, listed) == ""
        said = Call.code_problem({"packages": ["pandas", "numpy"]}, listed)
        assert "'numpy' is not on the list" in said and "pandas, typing-extensions" in said
        assert "list is: empty" in Call.code_problem({"packages": ["pandas"]}, [])

    def test_no_list_is_any_package(self):
        assert Call.code_problem({"packages": ["numpy"]}, None) == ""
        assert Call("c_1").listed_packages() is None
        assert Call("c_1", safety={"packages": "listed", "allowed_packages": ["x"]}
                    ).listed_packages() == ["x"]

    def test_a_name_is_compared_as_an_index_compares_it(self):
        assert Call.package("Typing_Extensions>=4.0") == "typing-extensions"
        assert Call.package("requests[socks]==2.32.3") == "requests"
        assert Call.package("ruamel.yaml") == "ruamel-yaml"


class TestACardThePersonAllowed:
    def test_its_hosts_are_open_until_the_call_ends(self):
        place = Place()
        call = placed(place)
        call.allow({"hosts": ["api.example.com"], "packages": []})
        call.allow({"hosts": ["files.example.com"], "packages": []})
        assert place.open == [("api.example.com", None), ("files.example.com", None)]
        call.close()
        assert place.open == []
        # Closed once: closing again gives nothing back twice.
        call.close()

    def test_its_packages_may_be_installed_and_no_others(self):
        call = placed(Place())
        call.allow({"hosts": [], "packages": ["pandas", "requests==2.32.3"]})
        assert call.refused(["pandas"]) is None
        assert call.refused(["requests==2.32.3", "pandas"]) is None
        # As the card wrote it: another version is another package.
        assert call.refused(["pandas", "requests"]) == "requests"
        assert call.refused(["numpy"]) == "numpy"

    def test_only_a_function_that_runs_code_is_granted_anything(self):
        async def propose(code, source):
            return True

        place = Place()
        consent = placed(place, sinks=ChatSinks(propose=propose))
        assert run(consent.propose({"language": "python", "code": "x", "purpose": "p",
                                    "hosts": ["api.example.com"]})) is True
        assert place.open == [] and consent.packages == set()
        granted = placed(place, function_spec={"code": True},
                         sinks=ChatSinks(propose=propose))
        assert run(granted.propose({"language": "python", "code": "x", "purpose": "p",
                                    "hosts": ["api.example.com"],
                                    "packages": ["pandas"]})) is True
        assert place.open == [("api.example.com", None)]
        assert granted.packages == {"pandas"}
