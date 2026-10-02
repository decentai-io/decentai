"""What a person allowed for one call of a function that runs code
(execution/code_grant.py): the names are checked before a card is
shown, an allowed card opens its hosts on the worker's way out and
makes its packages installable, and the call's end closes what it
opened."""

from types import SimpleNamespace

from ai_runtime.execution.code_grant import CodeGrant


class Place:
    """Stands for a confined worker's place: remembers what it lent."""

    def __init__(self):
        self.open = []

    def lend(self, hosts):
        lent = [(host, None) for host in hosts]
        self.open.extend(lent)
        return lent

    def take_back(self, lent):
        for one in lent:
            self.open.remove(one)


def granted(place):
    grant = CodeGrant()
    grant.context = SimpleNamespace(handle=SimpleNamespace(place=place))
    return grant


class TestWhatACardMayName:
    def test_a_host_is_one_name_and_perhaps_a_port(self):
        assert CodeGrant.host("API.Example.com") == "api.example.com"
        assert CodeGrant.host("db.example.com:5432") == "db.example.com:5432"
        for written in ("*.example.com", "10.0.0.7", "[::1]", "localhost",
                        "example.com:0", "example.com:70000",
                        "https://example.com", "example.com/path", ""):
            assert CodeGrant.host(written) == "", written

    def test_a_proposal_naming_what_cannot_be_allowed_says_which(self):
        assert CodeGrant.problem({"hosts": ["api.example.com"],
                                  "packages": ["pandas", "requests==2.32.3"]}) == ""
        assert "'*.example.com' is not a host" in CodeGrant.problem(
            {"hosts": ["api.example.com", "*.example.com"]})
        assert "'git+https://example.com/x' is not a package" in CodeGrant.problem(
            {"packages": ["git+https://example.com/x"]})


class TestAListOfPackages:
    """Where the deployment keeps a list (Settings:Safety), a program
    may install what is on it and nothing else."""

    def test_a_package_off_the_list_is_refused_before_a_card(self):
        listed = ["pandas", "typing-extensions"]
        assert CodeGrant.problem(
            {"packages": ["Pandas==2.2", "typing_extensions>=4"]}, listed) == ""
        said = CodeGrant.problem({"packages": ["pandas", "numpy"]}, listed)
        assert "'numpy' is not on the list" in said and "pandas, typing-extensions" in said
        assert "list is: empty" in CodeGrant.problem({"packages": ["pandas"]}, [])

    def test_no_list_is_any_package(self):
        assert CodeGrant.problem({"packages": ["numpy"]}, None) == ""

    def test_a_name_is_compared_as_an_index_compares_it(self):
        assert CodeGrant.package("Typing_Extensions>=4.0") == "typing-extensions"
        assert CodeGrant.package("requests[socks]==2.32.3") == "requests"
        assert CodeGrant.package("ruamel.yaml") == "ruamel-yaml"


class TestACardThePersonAllowed:
    def test_its_hosts_are_open_until_the_call_ends(self):
        place = Place()
        grant = granted(place)
        grant.allow({"hosts": ["api.example.com"], "packages": []})
        grant.allow({"hosts": ["files.example.com"], "packages": []})
        assert place.open == [("api.example.com", None), ("files.example.com", None)]
        grant.close()
        assert place.open == []
        # Closed once: closing again gives nothing back twice.
        grant.close()

    def test_its_packages_may_be_installed_and_no_others(self):
        grant = granted(Place())
        grant.allow({"hosts": [], "packages": ["pandas", "requests==2.32.3"]})
        assert grant.allowed(["pandas"]) is None
        assert grant.allowed(["requests==2.32.3", "pandas"]) is None
        # As the card wrote it: another version is another package.
        assert grant.allowed(["pandas", "requests"]) == "requests"
        assert grant.allowed(["numpy"]) == "numpy"

    def test_where_nothing_confines_a_card_still_allows_its_packages(self):
        grant = CodeGrant()
        grant.context = SimpleNamespace(handle=SimpleNamespace(place=None))
        grant.allow({"hosts": ["api.example.com"], "packages": ["pandas"]})
        assert grant.allowed(["pandas"]) is None
        grant.close()
