"""The starter for macOS (launcher/starter/macos/DecentAI.command).

The script itself is run, by bash — the one every Mac carries, or Git's
on Windows, which speaks the same. The programs it asks — docker,
podman, brew, defaults — are stood in for (starter_stub.py): each test
says what the machine has, and reads what the starter asked of it.
Against a real engine on a real Mac it is tried by hand.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.starter_stub import Stub

SCRIPT = (Path(__file__).resolve().parent.parent
          / "starter" / "macos" / "DecentAI.command")
IMAGE = "decentai-launcher:local"
SOCKET = "/run/user/501/podman/podman.sock"

LAUNCHER = [
    {"when": "image inspect", "code": 0, "out": "sha256:abc"},
    {"when": f"{IMAGE} status", "lacks": "installed", "code": 1,
     "out": "DecentAI is not installed here."},
    {"when": f"{IMAGE} status", "needs": "installed",
     "out": "DecentAI 0.1.0 - http://localhost:4280"},
    {"when": f"{IMAGE} install", "sets": "installed",
     "out": "DecentAI 0.1.0 is running. Open http://localhost:4280"},
    {"when": f"{IMAGE} start", "out": "DecentAI is running."},
]
DOCKER_RUNNING = [{"when": "version", "out": "29.6.1"}, *LAUNCHER]
#: Installed by Homebrew a moment ago: no machine yet, nothing running.
PODMAN_NEW = [
    {"when": "info", "needs": "started", "out": f"unix://{SOCKET}"},
    {"when": "info", "code": 125, "out": "Cannot connect to Podman"},
    {"when": "machine inspect", "needs": "made", "out": "stopped"},
    {"when": "machine inspect", "code": 125, "out": "no such machine"},
    {"when": "machine init", "sets": "made", "out": "made"},
    {"when": "machine start", "sets": "started", "out": "started"},
    *LAUNCHER]
PODMAN_BROKEN = [
    {"when": "info", "code": 125, "out": "Cannot connect to Podman"},
    {"when": "machine inspect", "out": "stopped"},
    {"when": "machine start", "code": 125, "out": "vfkit exited unexpectedly"},
]
BROWSERS = """(
        {
        LSHandlerContentType = "public.html";
        LSHandlerRoleAll = "com.apple.safari";
    },
        {
        LSHandlerURLScheme = http;
        LSHandlerPreferredVersions =         {
            LSHandlerRoleAll = "-";
        };
        LSHandlerRoleAll = "com.google.Chrome";
    },
)"""


def bash() -> str:
    """The bash to run the starter with: Git's on Windows (never the
    one that opens a Linux subsystem), the machine's own elsewhere."""
    if os.name == "nt":
        git = shutil.which("git")
        if git:
            for folder in Path(git).parents:
                candidate = folder / "bin" / "bash.exe"
                if candidate.exists():
                    return str(candidate)
        return ""
    return shutil.which("bash") or ""


pytestmark = pytest.mark.skipif(not bash(), reason="needs bash")


def posix(path: Path) -> str:
    """A path as bash says it: /c/Users/... for Git's on Windows."""
    text = Path(path).as_posix()
    if os.name == "nt" and len(text) > 1 and text[1] == ":":
        return f"/{text[0].lower()}{text[2:]}"
    return text


class Mac:
    """A Mac, as the starter finds it."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.programs = folder / "programs"
        self.programs.mkdir()
        self.scenario = {}
        tools = Path(bash()).parent.parent / "usr" / "bin" if os.name == "nt" else Path("/usr/bin")
        self.environment = {
            "PATH": os.pathsep.join([str(self.programs), str(tools), "/bin"]),
            "HOME": str(folder / "home"),
            "TEMP": str(folder), "TMP": str(folder), "TMPDIR": str(folder),
            "DECENTAI_STARTER_HOME": posix(folder / "kept"),
            "DECENTAI_PROGRAM_FOLDERS": posix(self.programs),
            "DECENTAI_DOCKER_APP": posix(folder / "no-docker.app"),
            "DECENTAI_APPLICATIONS": posix(folder / "Applications"),
            "DECENTAI_NO_BROWSER": "1",
            "DECENTAI_PATIENCE": "1",
            # Git's bash on Windows rewrites what looks like a Unix path
            # when it calls a Windows program — the stand-ins are Python.
            # A Mac never does; neither may the tests.
            "MSYS_NO_PATHCONV": "1",
            "MSYS2_ARG_CONV_EXCL": "*",
        }
        (folder / "home").mkdir()

    def has(self, program: str, rules: list) -> "Mac":
        self.scenario[program] = rules
        Stub(str(self.programs), program, []).install(program)
        return self

    def could_install(self, program: str, rules: list) -> "Mac":
        self.scenario[program] = rules
        return self

    def remembers(self, engine: str) -> "Mac":
        kept = self.folder / "kept"
        kept.mkdir(parents=True, exist_ok=True)
        (kept / "starter.conf").write_text(f"engine={engine}\n", encoding="ascii")
        return self

    def kept(self, name: str) -> str:
        found = self.folder / "kept" / "starter.conf"
        if not found.exists():
            return ""
        values = dict(line.split("=", 1) for line in
                      found.read_text(encoding="ascii").splitlines() if "=" in line)
        return values.get(name, "")

    def run(self, *arguments: str, **environment: str):
        (self.programs / "scenario.json").write_text(
            json.dumps(self.scenario), encoding="utf-8")
        done = subprocess.run(
            [bash(), posix(SCRIPT), *arguments],
            env={**self.environment, **environment}, capture_output=True,
            text=True, stdin=subprocess.DEVNULL, timeout=120)
        return done.returncode, done.stdout + done.stderr

    def call(self, function: str, *arguments: str) -> str:
        """One part of the starter, read rather than run."""
        (self.programs / "scenario.json").write_text(
            json.dumps(self.scenario), encoding="utf-8")
        done = subprocess.run(
            [bash(), "-c", f'source "$1"; shift; {function} "$@"', "starter-test",
             posix(SCRIPT), *arguments],
            env=self.environment, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=60)
        return done.stdout.strip()

    def asked(self, program: str = "") -> list:
        found = self.programs / "asked.jsonl"
        if not found.exists():
            return []
        rows = [json.loads(line) for line in found.read_text(encoding="utf-8").splitlines()]
        return [" ".join(row["arguments"]) for row in rows
                if not program or row["program"] == program]

    def launched(self, program: str) -> list:
        """What the launcher was asked to do, in order."""
        return [line.split(IMAGE)[1].strip() for line in self.asked(program)
                if line.startswith("run ") and IMAGE in line]


@pytest.fixture
def mac(tmp_path):
    return Mac(tmp_path)


class TestWhichEngine:
    def test_podman_where_there_is_nothing(self, mac):
        code, said = mac.run("engine")
        assert code == 0
        assert said.startswith("Podman: Nothing that runs containers is running on this Mac.")

    def test_docker_where_it_runs(self, mac):
        mac.has("docker", DOCKER_RUNNING)
        code, said = mac.run("engine")
        assert said.startswith("Docker: Docker is running on this Mac.")

    def test_the_engine_an_install_was_made_on(self, mac):
        mac.has("docker", DOCKER_RUNNING).remembers("podman")
        code, said = mac.run("engine")
        assert said.startswith("Podman: DecentAI was installed on Podman")


class TestTheFirstRun:
    def test_on_docker_it_installs_remembers_and_shows(self, mac):
        mac.has("docker", DOCKER_RUNNING)
        code, said = mac.run(DECENTAI_ANSWER="no")
        assert code == 0, said
        assert mac.launched("docker") == ["status", "install", "status"]
        install = [line for line in mac.asked("docker") if f"{IMAGE} install" in line][0]
        assert "-v /var/run/docker.sock:/var/run/docker.sock" in install
        assert "-v decentai_launcher:/state" in install
        assert mac.kept("engine") == "docker"
        assert "Open http://localhost:4280" in said

    def test_once_installed_it_starts(self, mac):
        mac.has("docker", DOCKER_RUNNING).remembers("docker")
        (mac.programs / "installed.flag").write_text("")
        code, said = mac.run(DECENTAI_ANSWER="no")
        assert code == 0, said
        assert mac.launched("docker") == ["status", "start", "status"]

    def test_with_nothing_it_installs_podman_with_homebrew_after_asking(self, mac):
        mac.has("brew", [{"when": "install podman", "installs": "podman"}])
        mac.could_install("podman", PODMAN_NEW)
        code, said = mac.run(DECENTAI_ANSWER="yes")
        assert code == 0, said
        assert mac.asked("brew") == ["install podman"]
        podman = mac.asked("podman")
        assert "machine init" in podman and "machine start" in podman
        install = [line for line in podman if f"{IMAGE} install" in line][0]
        assert f"-v {SOCKET}:/var/run/docker.sock" in install
        assert mac.kept("engine") == "podman"

    def test_a_no_installs_nothing(self, mac):
        mac.has("brew", [{"when": "install podman", "installs": "podman"}])
        code, said = mac.run(DECENTAI_ANSWER="no")
        assert code == 1
        assert "Nothing was installed." in said
        assert mac.asked("brew") == []

    def test_without_homebrew_it_says_where_podman_is(self, mac):
        code, said = mac.run()
        assert code == 1
        assert "https://podman.io" in said
        assert mac.asked() == []

    def test_a_build_of_ones_own_is_said_to_be_one(self, mac):
        mac.has("docker", DOCKER_RUNNING)
        mac.run(DECENTAI_ANSWER="no", DECENTAI_UNSIGNED="1", DECENTAI_PORT="4281",
                DECENTAI_PROJECT="decentai-trial")
        install = [line for line in mac.asked("docker") if f"{IMAGE} install" in line][0]
        assert install.endswith(f"{IMAGE} install --unsigned --port 4281")
        assert "-e DECENTAI_PROJECT=decentai-trial" in install


class TestAnEngineThatDoesNotStart:
    def test_the_install_is_not_looked_for_elsewhere(self, mac):
        mac.has("docker", DOCKER_RUNNING).has("podman", PODMAN_BROKEN).remembers("podman")
        code, said = mac.run()
        assert code == 1
        assert "vfkit exited unexpectedly" in said
        assert "installed on Podman, which did not start" in said
        assert not any(line.startswith("run ") for line in mac.asked("docker"))


class TestWhatIsPassedOn:
    def test_a_new_password_is_handed_over_by_name(self, mac):
        mac.has("docker", DOCKER_RUNNING).remembers("docker")
        code, said = mac.run("reset-password", "--email", "sara@example.com",
                             DECENTAI_PASSWORD="Trial-password-78")
        assert code == 0, said
        reset = [line for line in mac.asked("docker") if f"{IMAGE} reset-password" in line][0]
        assert reset.endswith(f"{IMAGE} reset-password --email sara@example.com")
        assert "-e DECENTAI_PASSWORD " in reset
        assert "Trial-password-78" not in "\n".join(mac.asked())

    def test_an_unknown_word_says_what_there_is(self, mac):
        code, said = mac.run("dance")
        assert code == 2 and "reset-password" in said


class TestAFolderToDevelopIn:
    def test_off_takes_it_back(self, mac):
        mac.has("docker", DOCKER_RUNNING).remembers("docker")
        code, _ = mac.run("develop", "off")
        assert code == 0
        assert mac.launched("docker") == ["develop --off"]

    def test_a_folder_that_is_not_there(self, mac):
        mac.has("docker", DOCKER_RUNNING).remembers("docker")
        code, said = mac.run("develop", posix(mac.folder / "nowhere"))
        assert code == 1 and "There is no folder" in said

    def test_a_folder_the_engine_does_not_share(self, mac):
        mac.has("docker", DOCKER_RUNNING).remembers("docker")
        code, said = mac.run("develop", posix(mac.folder))
        assert code == 1 and "not in a folder Docker shares" in said

    def test_each_engine_finds_a_home_folder_as_it_is(self, mac):
        assert mac.call("docker_finds", "/Users/sara/agents") == "/Users/sara/agents"
        assert mac.call("podman_finds", "/Users/sara/agents") == "/Users/sara/agents"
        assert mac.call("docker_finds", "/Volumes/Work/agents") == "/Volumes/Work/agents"
        assert mac.call("podman_finds", "/Volumes/Work/agents") == ""


class TestTheWindow:
    def test_the_persons_own_browser_is_read_whatever_the_order(self, mac):
        mac.has("defaults", [{"when": "LSHandlers", "out": BROWSERS}])
        assert mac.call("own_browser") == "com.google.chrome"

    def test_no_handler_for_the_web_is_no_browser(self, mac):
        mac.has("defaults", [{"when": "LSHandlers", "out": "(\n)"}])
        assert mac.call("own_browser") == ""


class TestTheApp:
    def test_it_is_made_in_applications(self, mac):
        code, said = mac.run("shortcuts")
        assert code == 0, said
        app = mac.folder / "Applications" / "DecentAI.app" / "Contents"
        info = (app / "Info.plist").read_text(encoding="utf-8")
        assert "<key>CFBundleExecutable</key><string>DecentAI</string>" in info
        runs = (app / "MacOS" / "DecentAI").read_text(encoding="utf-8")
        assert posix(SCRIPT) in runs and " open " in runs
        assert mac.kept("shortcuts") == "made"

    def test_offered_once_and_a_no_is_remembered(self, mac):
        mac.has("docker", DOCKER_RUNNING)
        mac.run(DECENTAI_ANSWER="no")
        assert mac.kept("shortcuts") == "declined"
        assert not (mac.folder / "Applications" / "DecentAI.app").exists()
