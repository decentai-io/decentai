"""The starter for Windows (launcher/starter/windows/DecentAI.ps1).

The script itself is run, by the PowerShell every Windows carries. The
programs it asks — docker, podman, wsl, winget — are stood in for
(starter_stub.py): each test says what the machine has, and reads what
the starter asked of it. Against a real engine it is tried by hand.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.starter_stub import Stub

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="the starter for Windows runs on Windows")

SCRIPT = (Path(__file__).resolve().parent.parent
          / "starter" / "windows" / "DecentAI.ps1")
IMAGE = "decentai-launcher:local"
SOCKET = "/run/user/1000/podman/podman.sock"

#: The launcher, as the engine that runs it answers for it.
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
DOCKER_STOPPED = [{"when": "version", "code": 1, "out": "cannot connect"}]
PODMAN_RUNNING = [
    {"when": "info", "out": f"unix://{SOCKET}"}, *LAUNCHER]
#: Installed, its machine made, and stopped.
PODMAN_STOPPED = [
    {"when": "info", "needs": "started", "out": f"unix://{SOCKET}"},
    {"when": "info", "code": 125, "out": "Cannot connect to Podman"},
    {"when": "machine inspect", "out": "stopped"},
    {"when": "machine start", "sets": "started", "out": "started"},
    *LAUNCHER]
WSL_NEW = [{"when": "--version", "out": "WSL version: 2.7.14.0"}]
WSL_OLD = [{"when": "--version", "out": "WSL version: 2.1.5.0"},
           {"when": "--update", "sets": "updated", "out": "updated"}]


class Machine:
    """A computer, as the starter finds it."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.programs = folder / "programs"
        self.programs.mkdir()
        self.scenario = {}
        self.environment = {
            "PATH": os.pathsep.join([
                str(self.programs),
                r"C:\Windows\System32", r"C:\Windows",
                r"C:\Windows\System32\WindowsPowerShell\v1.0"]),
            "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
            "COMSPEC": os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe"),
            "PATHEXT": ".COM;.EXE;.BAT;.CMD",
            # What Windows itself reads to find its own folders: without
            # them it makes folders named after the variables, here.
            **{name: os.environ[name] for name in (
                "SystemDrive", "ProgramData", "windir", "USERPROFILE",
                "APPDATA", "PUBLIC") if name in os.environ},
            "ProgramFiles": os.environ.get("ProgramFiles", r"C:\Program Files"),
            "TEMP": str(folder), "TMP": str(folder),
            # Nothing of the real machine's: no winget, no Podman, no
            # Docker Desktop, and a starter that remembers nothing.
            "LOCALAPPDATA": str(folder / "local"),
            "DECENTAI_PODMAN_HOME": str(folder / "no-podman"),
            "DECENTAI_DOCKER_DESKTOP": str(folder / "no-docker-desktop.exe"),
            "DECENTAI_NO_BROWSER": "1",
            "DECENTAI_PATIENCE": "1",
            # Never the real desktop or the real Start menu.
            "DECENTAI_SHORTCUTS_HOME": str(folder / "shortcuts"),
        }

    def has(self, program: str, rules: list) -> "Machine":
        self.scenario[program] = rules
        Stub(str(self.programs), program, []).install(program)
        return self

    def could_install(self, program: str, rules: list) -> "Machine":
        """What the program will answer once something installs it."""
        self.scenario[program] = rules
        return self

    def remembers(self, engine: str) -> "Machine":
        kept = self.folder / "local" / "DecentAI"
        kept.mkdir(parents=True)
        (kept / "starter.json").write_text(
            json.dumps({"engine": engine}), encoding="ascii")
        return self

    def remembered(self) -> str:
        kept = self.folder / "local" / "DecentAI" / "starter.json"
        if not kept.exists():
            return ""
        return json.loads(kept.read_text(encoding="utf-8-sig"))["engine"]

    def run(self, *arguments: str, answer: str = "", **environment: str):
        (self.programs / "scenario.json").write_text(
            json.dumps(self.scenario), encoding="utf-8")
        given = {**self.environment, **environment}
        if answer:
            given["DECENTAI_ANSWER"] = answer
        done = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(SCRIPT), *arguments],
            env=given, capture_output=True, text=True, timeout=120,
            stdin=subprocess.DEVNULL)
        return done.returncode, done.stdout + done.stderr

    def shortcuts(self) -> list:
        """The shortcuts that were made, as desktop/DecentAI.lnk."""
        home = self.folder / "shortcuts"
        return sorted(str(path.relative_to(home)).replace("\\", "/")
                      for path in home.rglob("*.lnk")) if home.exists() else []

    def kept(self) -> dict:
        kept = self.folder / "local" / "DecentAI" / "starter.json"
        return json.loads(kept.read_text(encoding="utf-8-sig")) if kept.exists() else {}

    def asked(self, program: str = "") -> list:
        """What was asked, each as one line of words."""
        log = self.programs / "asked.jsonl"
        if not log.exists():
            return []
        rows = [json.loads(line) for line in
                log.read_text(encoding="utf-8").splitlines()]
        return [" ".join(row["arguments"]) for row in rows
                if not program or row["program"] == program]


@pytest.fixture
def machine(tmp_path):
    return Machine(tmp_path).has("wsl", WSL_NEW)


class TestWhichEngine:
    def test_docker_where_it_runs(self, machine):
        machine.has("docker", DOCKER_RUNNING).has("podman", PODMAN_RUNNING)
        code, said = machine.run("engine")
        assert code == 0 and said.startswith("Docker: Docker is running")

    def test_podman_where_docker_does_not_run(self, machine):
        machine.has("docker", DOCKER_STOPPED).has("podman", PODMAN_RUNNING)
        code, said = machine.run("engine")
        assert code == 0 and said.startswith("Podman: Docker is not running")

    def test_podman_where_there_is_nothing(self, machine):
        code, said = machine.run("engine")
        assert code == 0 and said.startswith("Podman: Nothing that runs containers")
        # Asking which engine installs nothing and starts nothing.
        assert machine.asked("winget") == []

    def test_the_engine_an_install_was_made_on(self, machine):
        machine.has("docker", DOCKER_RUNNING).has("podman", PODMAN_RUNNING)
        machine.remembers("podman")
        code, said = machine.run("engine")
        assert code == 0 and said.startswith("Podman: DecentAI was installed on Podman")


class TestTheFirstRun:
    def test_on_docker_it_installs_remembers_and_shows(self, machine):
        machine.has("docker", DOCKER_RUNNING)
        code, said = machine.run("open")
        assert code == 0, said
        runs = [line for line in machine.asked("docker") if line.startswith("run ")]
        assert [line.split(IMAGE)[1].strip() for line in runs] == [
            "status", "install", "status"]
        assert "-v /var/run/docker.sock:/var/run/docker.sock" in runs[1]
        assert "-v decentai_launcher:/state" in runs[1]
        assert machine.remembered() == "docker"
        assert "Open http://localhost:4280" in said

    def test_after_that_it_starts(self, machine):
        machine.has("docker", DOCKER_RUNNING)
        assert machine.run("open")[0] == 0
        (machine.programs / "asked.jsonl").unlink()
        code, said = machine.run("open")
        assert code == 0, said
        runs = [line.split(IMAGE)[1].strip()
                for line in machine.asked("docker") if line.startswith("run ")]
        assert runs == ["status", "start", "status"]

    def test_on_podman_the_launcher_is_handed_podmans_socket(self, machine):
        machine.has("docker", DOCKER_STOPPED).has("podman", PODMAN_RUNNING)
        code, said = machine.run("open")
        assert code == 0, said
        install = [line for line in machine.asked("podman")
                   if f"{IMAGE} install" in line]
        assert len(install) == 1
        assert f"-v {SOCKET}:/var/run/docker.sock" in install[0]
        assert machine.remembered() == "podman"
        # Docker was asked whether it runs, and nothing else.
        assert machine.asked("docker") == ["version --format {{.Server.Version}}"]

    def test_a_build_of_ones_own_is_said_to_be_one(self, machine):
        machine.has("docker", DOCKER_RUNNING)
        code, _ = machine.run("open", DECENTAI_UNSIGNED="1", DECENTAI_PORT="4281",
                              DECENTAI_PROJECT="decentai-trial")
        assert code == 0
        install = [line for line in machine.asked("docker")
                   if f"{IMAGE} install" in line][0]
        assert install.endswith(f"{IMAGE} install --unsigned --port 4281")
        assert "-e DECENTAI_PROJECT=decentai-trial" in install

    def test_a_password_is_handed_over_by_name(self, machine):
        machine.has("docker", DOCKER_RUNNING)
        code, said = machine.run("open", DECENTAI_PASSWORD="Trial-password-77")
        assert code == 0
        install = [line for line in machine.asked("docker")
                   if f"{IMAGE} install" in line][0]
        assert "-e DECENTAI_PASSWORD " in install
        assert "Trial-password-77" not in "\n".join(machine.asked())
        assert "Trial-password-77" not in said

    def test_a_new_password_is_handed_over_by_name_too(self, machine):
        machine.has("docker", DOCKER_RUNNING).remembers("docker")
        code, said = machine.run("reset-password", "--email", "sara@example.com",
                                 DECENTAI_PASSWORD="Trial-password-78")
        assert code == 0, said
        reset = [line for line in machine.asked("docker")
                 if f"{IMAGE} reset-password" in line][0]
        assert reset.endswith(f"{IMAGE} reset-password --email sara@example.com")
        assert "-e DECENTAI_PASSWORD " in reset
        assert "Trial-password-78" not in "\n".join(machine.asked())

    def test_a_launcher_that_is_nowhere_is_said(self, machine):
        machine.has("docker", [
            {"when": "version", "out": "29.6.1"},
            {"when": "image inspect", "code": 1},
            {"when": "pull", "code": 1, "out": "not found"}])
        code, said = machine.run("open")
        assert code == 1
        assert "could not be" in said and IMAGE in said
        assert not any(line.startswith("run ") for line in machine.asked("docker"))


class TestAMachineWithNoEngine:
    def test_podman_is_installed_after_asking(self, machine):
        machine.has("winget", [{"when": "install", "installs": "podman"}])
        machine.could_install("podman", [
            {"when": "info", "needs": "started", "out": f"unix://{SOCKET}"},
            {"when": "info", "code": 125},
            {"when": "machine inspect", "needs": "made", "out": "stopped"},
            {"when": "machine inspect", "code": 125},
            {"when": "machine init", "sets": "made"},
            {"when": "machine start", "sets": "started"},
            *LAUNCHER])
        code, said = machine.run("open", answer="yes")
        assert code == 0, said
        assert len(machine.asked("winget")) == 1
        assert "--id RedHat.Podman --exact" in machine.asked("winget")[0]
        asked = machine.asked("podman")
        assert asked.index("machine init") < asked.index("machine start")
        assert machine.remembered() == "podman"
        assert "Open http://localhost:4280" in said

    def test_a_person_who_says_no_has_nothing_installed(self, machine):
        machine.has("winget", [{"when": "install", "installs": "podman"}])
        code, said = machine.run("open", answer="no")
        assert code == 1
        assert "Nothing was installed." in said
        assert machine.asked("winget") == []
        assert machine.remembered() == ""

    def test_a_machine_without_winget_is_told_where_podman_is(self, machine):
        code, said = machine.run("open", answer="yes")
        assert code == 1
        assert "https://podman.io" in said


class TestPodmansMachine:
    def test_one_that_is_stopped_is_started(self, machine):
        machine.has("docker", DOCKER_STOPPED).has("podman", PODMAN_STOPPED)
        code, said = machine.run("open")
        assert code == 0, said
        assert "machine start" in machine.asked("podman")
        assert "machine init" not in machine.asked("podman")
        assert machine.asked("wsl") == []

    #: A machine that starts once the subsystem is up to date.
    NEEDS_AN_UPDATE = [
        {"when": "info", "needs": "started", "out": f"unix://{SOCKET}"},
        {"when": "info", "code": 125},
        {"when": "machine inspect", "out": "stopped"},
        {"when": "machine start", "needs": "updated", "sets": "started"},
        {"when": "machine start", "code": 125,
         "out": "machine did not transition into running state"},
        *LAUNCHER]

    def test_the_subsystem_is_updated_after_asking(self, machine):
        machine.has("wsl", WSL_OLD).has("podman", self.NEEDS_AN_UPDATE)
        code, said = machine.run("open", answer="yes")
        assert code == 0, said
        assert "Every container on this computer stops" in said
        assert machine.asked("wsl") == ["--version", "--update"]
        assert machine.asked("podman").count("machine start") == 2
        assert machine.remembered() == "podman"

    def test_a_person_who_says_no_has_nothing_changed(self, machine):
        machine.has("wsl", WSL_OLD).has("podman", self.NEEDS_AN_UPDATE)
        code, said = machine.run("open", answer="no")
        assert code == 1
        assert "Nothing was changed." in said
        assert machine.asked("wsl") == ["--version"]

    def test_a_subsystem_that_is_up_to_date_is_not_updated(self, machine):
        machine.has("podman", self.NEEDS_AN_UPDATE)
        code, said = machine.run("open", answer="yes")
        assert code == 1
        assert "is up to date (version 2.7.14)" in said
        assert machine.asked("wsl") == ["--version"]


class TestAnInstallIsLookedForWhereItWasMade:
    def test_made_on_docker_it_is_not_begun_again_on_podman(self, machine):
        """Docker ran the day DecentAI was installed and does not
        today. Podman is there and runs; the install is not in it."""
        machine.has("docker", DOCKER_STOPPED).has("podman", PODMAN_RUNNING)
        machine.remembers("docker")
        code, said = machine.run("open")
        assert code == 1
        assert "was installed on Docker, which did not start" in said
        assert machine.asked("podman") == []
        assert machine.remembered() == "docker"

    def test_made_on_podman_it_stays_there_when_docker_runs(self, machine):
        machine.has("docker", DOCKER_RUNNING).has("podman", PODMAN_STOPPED)
        machine.remembers("podman")
        (machine.programs / "installed.flag").write_text("")
        code, said = machine.run("open")
        assert code == 0, said
        assert machine.asked("docker") == []
        assert f"run --rm -i -v {SOCKET}:/var/run/docker.sock" in " | ".join(
            machine.asked("podman"))


class TestWhatIsPassedOn:
    @pytest.mark.parametrize("command", [
        "stop", "status", "backup", "stop-everything", "start"])
    def test_a_command_of_the_launchers(self, machine, command):
        machine.has("docker", DOCKER_RUNNING)
        (machine.programs / "installed.flag").write_text("")
        code, _ = machine.run(command)
        assert code == 0
        assert machine.asked("docker")[-1].endswith(f"{IMAGE} {command}")

    def test_what_follows_a_command_goes_with_it(self, machine):
        machine.has("docker", DOCKER_RUNNING)
        code, _ = machine.run("update", "--yes", DECENTAI_UNSIGNED="1")
        assert code == 0
        assert machine.asked("docker")[-1].endswith(
            f"{IMAGE} update --unsigned --yes")

    def test_how_the_launcher_ended_is_how_the_starter_ends(self, machine):
        machine.has("docker", [
            {"when": "version", "out": "29.6.1"},
            {"when": "image inspect"},
            {"when": f"{IMAGE} status", "code": 1,
             "out": "DecentAI is not installed here."}])
        code, said = machine.run("status")
        assert code == 1
        assert "DecentAI is not installed here." in said

    def test_a_word_it_does_not_know(self, machine):
        machine.has("docker", DOCKER_RUNNING)
        code, said = machine.run("uninstall")
        assert code == 2
        assert "stop | status | update" in said
        assert machine.asked() == []


class TestAFolderToDevelopIn:
    """`develop <folder>`: the launcher is told the folder the way the
    engine finds it, which only the starter knows."""

    @staticmethod
    def folder(machine) -> Path:
        found = machine.folder / "My Agents"
        found.mkdir()
        return found

    @staticmethod
    def expected(prefix: str, folder: Path) -> str:
        drive, rest = str(folder.resolve()).split(":", 1)
        return f"{prefix}/{drive.lower()}{rest.replace(chr(92), '/')}"

    def test_on_docker_it_is_said_as_docker_desktop_finds_it(self, machine):
        machine.has("docker", DOCKER_RUNNING)
        folder = self.folder(machine)
        code, said = machine.run("develop", str(folder))
        assert code == 0, said
        assert machine.asked("docker")[-1].endswith(
            f"{IMAGE} develop {self.expected('/run/desktop/mnt/host', folder)}")

    def test_on_podman_it_is_said_as_its_machine_finds_it(self, machine):
        machine.has("docker", DOCKER_STOPPED).has("podman", PODMAN_RUNNING)
        folder = self.folder(machine)
        code, said = machine.run("develop", str(folder))
        assert code == 0, said
        assert machine.asked("podman")[-1].endswith(
            f"{IMAGE} develop {self.expected('/mnt', folder)}")

    def test_off_and_nothing(self, machine):
        machine.has("docker", DOCKER_RUNNING)
        assert machine.run("develop", "off")[0] == 0
        assert machine.asked("docker")[-1].endswith(f"{IMAGE} develop --off")
        assert machine.run("develop")[0] == 0
        assert machine.asked("docker")[-1].endswith(f"{IMAGE} develop")

    def test_a_folder_that_is_not_there_is_said_and_nothing_runs(self, machine):
        machine.has("docker", DOCKER_RUNNING)
        code, said = machine.run("develop", str(machine.folder / "nowhere"))
        assert code == 1 and "There is no folder" in said
        assert not any(" develop" in line for line in machine.asked("docker"))


class TestTheWindow:
    """DecentAI is shown in a window of its own, not as an address in a
    tab."""

    def test_it_is_opened_as_an_app_window_at_the_installs_address(self, machine):
        import time

        machine.has("docker", DOCKER_RUNNING).has("browser", [])
        code, said = machine.run(
            "open", DECENTAI_NO_BROWSER="",
            DECENTAI_WINDOW_PROGRAM=str(machine.programs / "browser.cmd"))
        assert code == 0, said
        # The window is opened and not waited for.
        deadline = time.time() + 15
        while not machine.asked("browser") and time.time() < deadline:
            time.sleep(0.2)
        assert machine.asked("browser") == ["--app=http://localhost:4280"]
        # The starter names no address to go to: it showed the window.
        assert "Open http://localhost:4280" not in said.splitlines()


class TestOnTheDesktop:
    """A shortcut on the desktop and in the Start menu, offered once."""

    def test_nobody_there_to_answer_is_not_asked(self, machine):
        machine.has("docker", DOCKER_RUNNING)
        code, said = machine.run("open")
        assert code == 0
        assert "on the desktop" not in said
        assert machine.shortcuts() == [] and "shortcuts" not in machine.kept()

    def test_a_yes_makes_them_and_is_not_asked_again(self, machine):
        machine.has("docker", DOCKER_RUNNING)
        code, said = machine.run("open", answer="yes")
        assert code == 0, said
        assert "DecentAI is on the desktop and in the Start menu." in said
        assert machine.shortcuts() == ["desktop/DecentAI.lnk", "programs/DecentAI.lnk"]
        assert machine.kept() == {"engine": "docker", "shortcuts": "made"}
        again, said = machine.run("open", answer="yes")
        assert again == 0 and "on the desktop" not in said

    def test_a_no_is_remembered_and_says_how_to_change_it(self, machine):
        machine.has("docker", DOCKER_RUNNING)
        code, said = machine.run("open", answer="no")
        assert code == 0
        assert "DecentAI.cmd shortcuts" in said
        assert machine.shortcuts() == []
        assert machine.kept() == {"engine": "docker", "shortcuts": "declined"}
        again, said = machine.run("open", answer="yes")
        assert again == 0 and machine.shortcuts() == []

    def test_they_can_be_asked_for_by_name(self, machine):
        code, said = machine.run("shortcuts")
        assert code == 0, said
        assert machine.shortcuts() == ["desktop/DecentAI.lnk", "programs/DecentAI.lnk"]
        assert machine.kept()["shortcuts"] == "made"
        # Asking for them starts nothing and installs nothing.
        assert machine.asked() == []

    def test_a_shortcut_opens_the_starter(self, machine):
        machine.run("shortcuts")
        made = machine.folder / "shortcuts" / "desktop" / "DecentAI.lnk"
        read = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{made}'); "
             f"$s.TargetPath; $s.IconLocation"],
            capture_output=True, text=True, timeout=60)
        target, icon = read.stdout.strip().splitlines()[:2]
        assert target.lower() == str(SCRIPT.with_name("DecentAI.cmd")).lower()
        assert icon.lower().startswith(str(SCRIPT.with_name("DecentAI.ico")).lower())


class TestTheScript:
    def test_it_is_plain_ascii(self):
        """Windows PowerShell reads a script without a byte order mark
        in the machine's own code page."""
        SCRIPT.read_bytes().decode("ascii")

    def test_the_stub_is_this_interpreter(self):
        assert Path(sys.executable).exists()
