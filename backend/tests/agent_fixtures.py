"""The fixtures an agent install test stands on, shared by the backend
suite and the spanning one: the notebook fixture manifest, and a
stand-in for the git remote (never for the platform).
"""

import pytest
import yaml

from ai_runtime.tests.fixture_agents import AGENTS_DIR

MANIFEST_PATH = AGENTS_DIR / "notebook" / "manifest.yaml"


@pytest.fixture()
def manifest_doc():
    with open(MANIFEST_PATH, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@pytest.fixture()
def control(tmp_path):
    """Stand in for a git remote, not for the platform.

    Acquisition is the backend's now, so the seam moved down to the one
    thing a test cannot really do: reach a repository. Everything above
    it runs for real — the catalog is discovered, the agent's folder is
    packaged canonically, the digest is computed, the package is stored,
    and the runtime is handed the bytes. Tests set what the "repository"
    contains and assert what the platform did with it.
    """
    from api.services.agents.repository import Repository, RepositoryError

    state = {"manifest": None, "sha": "a" * 40, "calls": [], "fail": None,
             "files": {}}
    clones = tmp_path / "clones"
    clones.mkdir()

    def fetch(self, url, ref="", into=None, credential=None):
        state["calls"].append(("inspect", {
            "url": url, "ref": ref, "credential": credential,
        }))
        if state["fail"] and state["fail"][0] == "inspect":
            raise RepositoryError(state["fail"][1])
        folder = clones / f"clone-{len(state['calls'])}"
        folder.mkdir()
        (folder / "manifest.yaml").write_text(
            yaml.safe_dump(state["manifest"] or {}), encoding="utf-8")
        for name, body in (state["files"] or {}).items():
            path = folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        return folder, state["sha"]

    original = Repository.fetch
    Repository.fetch = fetch
    yield state
    Repository.fetch = original

