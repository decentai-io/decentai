"""The fixture agents, as REAL installed agents.

No in-process imports anywhere: every fixture agent is an
InstalledAgent — manifest, folder, and a private environment — and
executing its functions spawns a real worker over the real wire,
exactly as production does. The one economy is the environment: the
fixture agents declare no dependencies, so they all share a single
cached venv (``backend/tests/.workerenv``, gitignored) that is built
once and reused across runs.
"""

import shutil
import textwrap
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ai_runtime.agents import AgentEnvironment, InstalledAgent
from contracts.agent_manifest import load_manifest

AGENTS_DIR = Path(__file__).resolve().parent / "fixtures" / "agents"
WORKERENV_DIR = Path(__file__).resolve().parent / ".workerenv"


def worker_environment() -> AgentEnvironment:
    """The shared fixture venv — built on first use, cached forever
    (the `.ready` marker short-circuits every later call)."""
    environment = AgentEnvironment(WORKERENV_DIR)
    if not environment.exists():
        errors = environment.build([])
        assert errors == [], errors
    else:
        _refresh_sdk(environment)
    return environment


def _refresh_sdk(environment: AgentEnvironment) -> None:
    """The venv is cached; the SDK in it must not be. A copy frozen at
    the first build would test yesterday's SDK against today's host."""
    import decentai_sdk

    source = Path(decentai_sdk.__file__).resolve().parent
    target = environment._site_packages()
    assert target is not None, "the cached venv has no site-packages"
    shutil.copytree(source, target / "decentai_sdk", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))


def load_agents(directory: Optional[Path] = None) -> Tuple[
        Dict[str, InstalledAgent], Dict[str, List[str]]]:
    """``(agents by id, errors by folder name)``. A folder without a
    manifest is not an agent and is skipped.

    Installed agents, addressed by their own id — unique per
    organization, so a grant in a test reads `notebook.note.save`,
    exactly as the manifest declares it."""
    directory = Path(directory) if directory is not None else AGENTS_DIR
    if not directory.is_dir():
        return {}, {}

    environment = worker_environment()
    agents: Dict[str, InstalledAgent] = {}
    errors: Dict[str, List[str]] = {}
    for folder in sorted(directory.iterdir()):
        if not folder.is_dir() or not (folder / "manifest.yaml").is_file():
            continue
        manifest, problems = load_manifest(folder / "manifest.yaml")
        if problems:
            errors[folder.name] = problems
            continue
        installed = InstalledAgent(
            f"fixture:{folder.name}", manifest, folder, environment
        )
        agents[installed.agent_id] = installed
    return agents, errors


# A minimal buildable agent, for tests that write their own package.
MINIMAL_MANIFEST = textwrap.dedent("""\
    schema_version: "1.0"
    agent:
      id: {agent_id}
      name: Demo
      version: "1.0.0"
      description: A test agent.
    network:
      hosts: []
    implementation:
      entrypoint: {entrypoint}
    tools:
      - id: main
        name: Main
        description: The one tool.
        functions:
          - id: run
            name: Run
            description: Do the thing.
            permission_level: 0
            inputs: {{type: object}}
            outputs: {{type: object}}
""")

WORKING_AGENT = textwrap.dedent("""\
    from decentai_sdk.base import AgentBase, ToolBase

    class MainTool(ToolBase):
        id = "main"

        async def run(self, call):
            return {"ok": True}, "success"

    class DemoAgent(AgentBase):
        def tools(self):
            return [MainTool(self)]
""")


def write_agent(directory, folder, manifest=None, agent_id=None, files=None):
    package = directory / folder
    package.mkdir(parents=True)
    (package / "manifest.yaml").write_text(
        manifest or MINIMAL_MANIFEST.format(
            agent_id=agent_id or folder, entrypoint="agent:DemoAgent"
        ),
        encoding="utf-8",
    )
    for name, content in (files or {"agent.py": WORKING_AGENT}).items():
        (package / name).write_text(content, encoding="utf-8")
    return package
