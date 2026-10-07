"""The documents, held to the repository they describe (docs/README.md:
"a change that contradicts one of these pages changes the page in the
same commit").

What a page says in prose is a reader's to check. What it points at is
checked here: a link leads to a page and to a heading on it, a path
named in a page is a path in the repository, a setting a page names is
one something reads, a setting the example files offer is on the
settings page, and the page that is generated is what its generator
writes today.

This is not the runtime's own, and it is here because this suite needs
nothing: no database, no network. It reads files and starts nothing.
"""

import importlib.util
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: Folders that are not the repository's own: made by a tool, or by
#: running the platform.
NOT_OURS = {".git", "node_modules", "dist", ".angular", ".venv", "venv",
            "__pycache__", ".pytest_cache", "installed_agents"}

#: The top of every path the repository keeps, so that a path in a page
#: is told from any other words with a slash between them.
TOPS = ("ai_runtime", "backend", "bootstrap", "brand", "contracts",
        "decentai_sdk", "docs", "examples", "frontend", "sim", "speech",
        "tests")

#: Paths a page names that are made where the platform runs, and are
#: not kept: a person's own settings, and the agents they installed.
MADE_NOT_KEPT = {"backend/config.env", "ai_runtime/config.env",
                 "ai_runtime/installed_agents"}

#: Where settings are offered to whoever sets the platform up.
EXAMPLES = ("deploy.env.example", "backend/config.env.example",
            "ai_runtime/config.env.example")

SETTINGS_PAGE = "docs/run/configuration.md"

#: What may read a setting: code, and the files a stack is started from.
READERS = (".py", ".yml", ".yaml", ".sh", ".c", ".example")
READERS_BY_NAME = ("Dockerfile", "Caddyfile")


class Repository:
    """The repository's files, walked once."""

    def __init__(self):
        self.pages = []
        self.readers = []
        for folder, folders, names in os.walk(ROOT):
            folders[:] = [name for name in folders if name not in NOT_OURS]
            for name in names:
                path = Path(folder) / name
                if name.endswith(".md"):
                    self.pages.append(path)
                elif name.endswith(READERS) or name in READERS_BY_NAME:
                    self.readers.append(path)
        self.pages.sort()

    @staticmethod
    def name(path: Path) -> str:
        return path.relative_to(ROOT).as_posix()

    @staticmethod
    def text(path: Path) -> str:
        return path.read_text(encoding="utf-8")

    @staticmethod
    def prose(text: str) -> str:
        """A page without its fenced examples: what is written there
        is shown, not pointed at."""
        return re.sub(r"```.*?```", "", text, flags=re.S)

    @staticmethod
    def anchor(heading: str) -> str:
        """A heading as the address a link reaches it by."""
        words = re.sub(r"[^\w\- ]", "", heading.strip().lower())
        return words.replace(" ", "-")

    def anchors(self, page: Path) -> set:
        found = set()
        for line in self.prose(self.text(page)).splitlines():
            if re.match(r"#{1,6} ", line):
                found.add(self.anchor(line.lstrip("#")))
        return found

    def read_somewhere(self, setting: str) -> bool:
        word = re.compile(rf"\b{re.escape(setting)}\b")
        return any(word.search(self.text(path)) for path in self.readers)


@pytest.fixture(scope="module")
def repository():
    return Repository()


class TestLinks:
    def test_every_link_leads_to_a_page_and_to_a_heading_on_it(self, repository):
        lost = []
        for page in repository.pages:
            prose = repository.prose(repository.text(page))
            for target in re.findall(r"\[[^\]]*\]\(([^)\s]+)\)", prose):
                # Another site, or an address inside the running app
                # (the guide's own links): not this repository's.
                if re.match(r"(https?:|mailto:|/)", target):
                    continue
                path, _, anchor = target.partition("#")
                reached = (page.parent / path).resolve() if path else page
                if not reached.exists():
                    lost.append(f"{repository.name(page)}: {target}")
                elif (anchor and reached.suffix == ".md"
                        and anchor not in repository.anchors(reached)):
                    lost.append(f"{repository.name(page)}: {target} "
                                "(no such heading)")
        assert lost == []


class TestPaths:
    def test_every_path_a_page_names_is_in_the_repository(self, repository):
        lost = []
        for page in repository.pages:
            for named in re.findall(r"`([A-Za-z_][\w\-./]*/[\w\-./]*)`",
                                    repository.text(page)):
                named = named.rstrip("/.")
                if named.split("/")[0] not in TOPS or named in MADE_NOT_KEPT:
                    continue
                if not (ROOT / named).exists():
                    lost.append(f"{repository.name(page)}: {named}")
        assert lost == []


class TestSettings:
    SETTING = r"`([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)`"

    def test_every_setting_the_settings_page_names_is_read_somewhere(
            self, repository):
        page = repository.text(ROOT / SETTINGS_PAGE)
        unread = sorted(
            setting for setting in set(re.findall(self.SETTING, page))
            if not repository.read_somewhere(setting))
        assert unread == []

    def test_every_setting_an_example_offers_is_on_the_settings_page(
            self, repository):
        page = repository.text(ROOT / SETTINGS_PAGE)
        missing = []
        for example in EXAMPLES:
            for line in repository.text(ROOT / example).splitlines():
                # Offered, or offered and left for a person to turn on.
                found = re.match(r"\s*#?\s*([A-Z][A-Z0-9_]+)=", line)
                if found and f"`{found.group(1)}`" not in page:
                    missing.append(f"{example}: {found.group(1)}")
        assert missing == []


class TestTheGeneratedPage:
    def test_the_actions_page_is_what_its_generator_writes(self, repository):
        """The page is written from the backend's catalog, so a new
        action is on it once the generator has been run again."""
        source = ROOT / "docs" / "reference" / "generate.py"
        spec = importlib.util.spec_from_file_location("docs_generate", source)
        generator = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(generator)
        except ImportError as missing:
            pytest.skip(f"the backend's catalog cannot be read here: {missing}")
        kept = repository.text(source.parent / "actions.md")
        assert kept == generator.page(), (
            "run: python docs/reference/generate.py")
