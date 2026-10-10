"""The harness: the agent as the platform installs it.

`agents` builds one virtual environment once per session, installs the
dependencies the catalog's manifests declare, all together, and hands
back an InstalledAgent for each agent. Functions then execute in a real worker process, over
the real worker protocol, against the platform's simulated resources —
no backend, no browser, no network.

The tests need the platform's code on the path, and the few packages
in tests/requirements.txt (docs/agents/developing.md). In the platform's
own repository, from its root:

    python -m pytest examples/tests -q

Copied beside an agent in a repository of your own:

    PYTHONPATH=/path/to/decentai python -m pytest tests -q
"""

from pathlib import Path

import pytest
import yaml

from ai_runtime.agents import AgentEnvironment, InstalledAgent
from contracts.agent_manifest import load_manifest



def _repository() -> Path:
    """The folder the catalog is in: the repository's root, wherever
    these tests sit beneath it."""
    for folder in Path(__file__).resolve().parents:
        if (folder / "decentai-agents.yaml").is_file():
            return folder
    raise RuntimeError("no decentai-agents.yaml above these tests")


ROOT = _repository()

#: Cached across runs — the `.ready` marker short-circuits the build.
#: Delete this directory after changing the manifest's dependencies.
WORKERENV_DIR = Path(__file__).resolve().parent / ".workerenv"


@pytest.fixture(scope="session")
def agents():
    """Every agent this catalog offers, by its own id."""
    catalog = yaml.safe_load(
        (ROOT / "decentai-agents.yaml").read_text(encoding="utf-8"))

    entries = []
    for entry in catalog["agents"]:
        folder = ROOT / entry["path"]
        manifest, errors = load_manifest(folder / "manifest.yaml")
        assert errors == [], f"{entry['id']}: {errors}"
        assert manifest.agent_id == entry["id"], (
            f"catalog says '{entry['id']}', manifest says '{manifest.agent_id}'")
        entries.append((folder, manifest))

    environment = AgentEnvironment(WORKERENV_DIR)
    if not environment.exists():
        dependencies = [d for _, m in entries for d in m.dependencies]
        from ai_runtime.tests.fake_container import standalone

        with standalone(WORKERENV_DIR.parent / ".workerenv-build") as own:
            errors = own.run(environment.build(dependencies, own.confinement.builder_place()))
        assert errors == [], errors

    return {
        manifest.agent_id: InstalledAgent(
            f"fixture:{manifest.agent_id}", manifest, folder, environment
        )
        for folder, manifest in entries
    }


@pytest.fixture(autouse=True)
def _agents_container(tmp_path):
    """Every test has an agents' container of its own, stood in for
    by the runtime's test double (ai_runtime/tests/fake_container.py):
    whatever it started dies with the test."""
    from ai_runtime.tests import fake_container

    fake_container.begin(tmp_path / "agents")
    yield
    fake_container.end()
