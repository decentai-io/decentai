"""The first run, a start and a stop, and an update with its way back."""

import json

import pytest

from launcher.engine import Engine
from launcher.installation import Installation, InstallationError
from launcher.release import Release
from launcher.settings import Settings, SettingsError
from tests.conftest import FakeEngine

EMAIL = "sara@example.com"
PASSWORD = "a-long-passphrase-7"


def release(version="1.4.0", signed=True, tag=None):
    tag = tag or version
    return Release({
        "schema_version": "1.0", "version": version,
        "images": {"backend": f"registry/backend:{tag}",
                   "runtime": f"registry/runtime:{tag}",
                   "frontend": f"registry/frontend:{tag}"},
    }, signed)


@pytest.fixture
def installation(engine, tmp_path):
    found = Installation(Settings(tmp_path / "state"),
                         Engine(tmp_path / "compose.yml"), say=lambda text: None)
    found.pause = 0
    found.HEALTHY_SECONDS = 1
    return found


@pytest.fixture
def installed(installation):
    installation.first_run(release("1.4.0"), EMAIL, PASSWORD, "Sara")
    installation.engine.runner.asked.clear()
    installation.engine.runner.pulled.clear()
    return installation


def state_files(installation):
    return {path.name: path.read_text(encoding="utf-8")
            for path in installation.settings.state.rglob("*") if path.is_file()}


class TestTheFirstRun:
    def test_it_pulls_makes_the_keys_seeds_starts_and_says_where(
            self, installation, engine):
        address = installation.first_run(release(), EMAIL, PASSWORD, "Sara", port=4280)
        assert address == "http://localhost:4280"
        assert engine.pulled == ["registry/backend:1.4.0", "registry/runtime:1.4.0",
                                 "registry/frontend:1.4.0"]
        assert engine.stack() == ["--profile seed run --rm init", "up -d"]
        install = installation.settings.install()
        assert install["version"] == "1.4.0" and install["port"] == 4280
        assert install["images"]["backend"] == "registry/backend:1.4.0"
        assert install["signed"] is True and install["previous"] is None

    def test_the_stack_is_filled_in_with_the_release_and_the_port(
            self, installation, engine):
        installation.first_run(release(), EMAIL, PASSWORD, port=5000)
        environment = engine.environment_of("up -d")
        assert environment["BACKEND_IMAGE"] == "registry/backend:1.4.0"
        assert environment["RUNTIME_IMAGE"] == "registry/runtime:1.4.0"
        assert environment["FRONTEND_IMAGE"] == "registry/frontend:1.4.0"
        assert environment["PORT"] == "5000"
        assert environment["STATE"] == str(installation.settings.state)
        # Never the name a developer's own stack goes by.
        assert environment["PROJECT"] == "decentai-app"

    def test_the_first_person_is_said_to_the_seeder_and_kept_nowhere(
            self, installation, engine):
        installation.first_run(release(), EMAIL, PASSWORD, "Sara")
        seeded = engine.environment_of("run --rm init")
        assert seeded["ADMIN_EMAIL"] == EMAIL
        assert seeded["ADMIN_PASSWORD"] == PASSWORD
        assert seeded["ADMIN_NAME"] == "Sara"
        assert "ADMIN_PASSWORD" not in engine.environment_of("up -d")
        for name, text in state_files(installation).items():
            assert PASSWORD not in text, name

    def test_the_runtime_is_handed_nothing_of_the_backends(self, installation):
        installation.first_run(release(), EMAIL, PASSWORD)
        files = state_files(installation)
        runtime = files[Settings.RUNTIME]
        assert "BACKEND_SERVICE_PUBLIC_KEY=-----BEGIN PUBLIC KEY-----" in runtime
        for held_back in ("PRIVATE_KEY", "SECRET_ENCRYPTION", "TOKEN_SECRET_KEY",
                          "MONGO", "private", "encryption-key"):
            assert held_back not in runtime, held_back
        backend = files[Settings.BACKEND]
        assert "BACKEND_SERVICE_PRIVATE_KEY=-----BEGIN PRIVATE KEY-----" in backend
        assert "SECRET_ENCRYPTION_KEYS=01:encryption-key" in backend
        assert "JWT_COOKIE_SECURE=false" in backend
        assert "PUBLIC_APP_URL=http://localhost:4280" in backend

    def test_the_database_and_the_backend_agree_on_the_password(self, installation):
        installation.first_run(release(), EMAIL, PASSWORD)
        files = state_files(installation)
        password = files[Settings.MONGO].split("MONGO_INITDB_ROOT_PASSWORD=")[1].strip()
        assert len(password) >= 32
        assert f"mongodb://decentai:{password}@mongo:27017" in files[Settings.BACKEND]

    @pytest.mark.parametrize("email, password, word", [
        ("sara", PASSWORD, "not an email"),
        (EMAIL, "short1", "at least 10"),
        (EMAIL, "nonumbersinthisone", "a letter and a number"),
        (EMAIL, "12345678901", "a letter and a number"),
        (EMAIL, " a-long-passphrase-7", "start or end with a space"),
    ])
    def test_a_first_person_the_platform_would_refuse_is_refused_first(
            self, installation, engine, email, password, word):
        with pytest.raises(InstallationError, match=word):
            installation.first_run(release(), email, password)
        assert engine.asked == []
        assert not installation.settings.state.exists()

    def test_an_engine_that_does_not_answer(self, installation, engine):
        engine.unreachable = "Cannot connect to the Docker daemon"
        with pytest.raises(InstallationError, match="does not answer"):
            installation.first_run(release(), EMAIL, PASSWORD)
        assert not installation.settings.installed

    def test_a_second_first_run_is_refused_and_changes_nothing(self, installed, engine):
        before = state_files(installed)
        with pytest.raises(InstallationError, match="already installed"):
            installed.first_run(release("1.5.0"), EMAIL, PASSWORD)
        assert state_files(installed) == before and engine.asked == []

    def test_a_build_of_ones_own_is_not_pulled_when_it_is_here(
            self, installation, engine):
        local = release("0.1.0", signed=False, tag="local")
        engine.present.update(local.images.values())
        installation.first_run(local, EMAIL, PASSWORD)
        assert engine.pulled == []
        assert installation.settings.install()["signed"] is False

    def test_it_is_not_installed_until_it_is_up(self, installation, engine):
        engine.states["backend"] = "exited"
        with pytest.raises(InstallationError, match="backend stopped instead of starting"):
            installation.first_run(release(), EMAIL, PASSWORD)
        assert not installation.settings.installed

    def test_a_first_run_that_did_not_finish_is_carried_on_with_its_keys(
            self, installation, engine):
        """The stack did not come up — an engine's quirk, a full disk.
        Running it again keeps the keys and the port the first try
        made, since the database was made with them."""
        engine.states["backend"] = "exited"
        with pytest.raises(InstallationError):
            installation.first_run(release(), EMAIL, PASSWORD, port=5000)
        assert installation.settings.begun
        before = {name: text for name, text in state_files(installation).items()
                  if name.endswith(".env")}
        engine.states["backend"] = "healthy"
        engine.asked.clear()

        address = installation.first_run(release(), EMAIL, PASSWORD)
        assert address == "http://localhost:5000"
        after = state_files(installation)
        assert {name: after[name] for name in before} == before
        assert engine.stack() == ["--profile seed run --rm init", "up -d"]
        assert installation.settings.installed and not installation.settings.begun


class TestWhereTheStackLooksNamesUp:
    """Podman on Windows cannot look up an address behind a long chain
    of aliases, so there the stack asks public resolvers."""

    def names(self, installation):
        path = installation.settings.names_file
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def test_on_docker_the_engines_own(self, installation, engine):
        installation.first_run(release(), EMAIL, PASSWORD)
        assert self.names(installation) == ""

    def test_on_podman_public_resolvers_for_the_two_that_connect_out(
            self, installation, engine):
        engine.podman = True
        installation.first_run(release(), EMAIL, PASSWORD)
        said = self.names(installation)
        assert said.count('dns: ["1.1.1.1", "8.8.8.8"]') == 2
        assert "backend:" in said and "ai-runtime:" in said and "mongo" not in said
        up = next(a["argv"] for a in engine.asked if a["argv"][-2:] == ["up", "-d"])
        assert str(installation.settings.names_file) in up

    def test_the_person_may_say_otherwise(self, installation, engine, monkeypatch):
        engine.podman = True
        monkeypatch.setenv("DECENTAI_DNS", "host")
        installation.first_run(release(), EMAIL, PASSWORD)
        assert self.names(installation) == ""

        monkeypatch.setenv("DECENTAI_DNS", "10.0.0.53, 10.0.0.54")
        engine.podman = False
        installation.start()
        assert 'dns: ["10.0.0.53", "10.0.0.54"]' in self.names(installation)

        monkeypatch.setenv("DECENTAI_DNS", "our-dns-server")
        with pytest.raises(Exception, match="takes addresses"):
            installation.start()


class TestEveryDay:
    def test_start_starts_what_is_installed(self, installed, engine):
        assert installed.start() == "http://localhost:4280"
        assert engine.stack() == ["up -d"]
        assert engine.environment_of("up -d")["BACKEND_IMAGE"] == "registry/backend:1.4.0"

    def test_stop_stops_and_removes_nothing(self, installed, engine):
        installed.stop()
        assert engine.stack() == ["stop"]
        assert installed.settings.installed

    def test_stop_everything_starts_the_runtime_again(self, installed, engine):
        installed.stop_everything()
        assert engine.stack() == ["restart ai-runtime"]

    def test_status_says_what_is_installed_and_what_is_running(self, installed, engine):
        engine.states["ai-runtime"] = "running starting"
        status = installed.status()
        assert status["version"] == "1.4.0" and status["address"] == "http://localhost:4280"
        assert status["services"] == {"mongo": "healthy", "backend": "healthy",
                                      "ai-runtime": "starting", "caddy": "healthy"}

    def test_a_stopped_container_is_stopped_whatever_it_last_said(
            self, installed, engine):
        engine.states = {service: "exited unhealthy"
                         for service in ("mongo", "backend", "ai-runtime", "caddy")}
        assert set(installed.status()["services"].values()) == {"exited"}

    def test_nothing_installed_is_said_not_raised(self, installation):
        assert installation.status() == {"installed": False}
        with pytest.raises(InstallationError, match="not installed here yet"):
            installation.start()

    def test_the_backend_is_told_it_is_on_a_desktop(self, installed):
        backend = state_files(installed)["backend.env"]
        assert "DEPLOYMENT_KIND=desktop\n" in backend
        # The backend's to know, and not the runtime's.
        assert "DEPLOYMENT_KIND" not in state_files(installed)["runtime.env"]

    def test_the_settings_are_made_once(self, installed):
        with pytest.raises(SettingsError, match="made once"):
            installed.settings.write(
                port=1, organization="x", token_key="t", service_private_key="a",
                service_public_key="b", encryption_keys="01:k", encryption_active="01")


class TestAnInstallMadeBeforeTheLauncherKnew:
    """An install whose settings were written by a launcher that did
    not say what kind of deployment it is."""

    @pytest.fixture
    def older(self, installed):
        path = installed.settings.state / Settings.BACKEND
        kept = [line for line in path.read_text(encoding="utf-8").splitlines()
                if not line.startswith("DEPLOYMENT_KIND=")]
        path.write_text("\n".join(kept) + "\n", encoding="utf-8", newline="\n")
        return installed

    def test_a_start_says_it(self, older):
        before = state_files(older)["backend.env"]
        older.start()
        after = state_files(older)["backend.env"]
        assert after == before + "DEPLOYMENT_KIND=desktop\n"

    def test_an_update_says_it_before_the_new_release_starts(self, older, engine):
        older.update(release("1.5.0"))
        assert "DEPLOYMENT_KIND=desktop\n" in state_files(older)["backend.env"]

    def test_nothing_that_was_there_is_changed(self, older):
        before = state_files(older)
        older.start()
        after = state_files(older)
        assert after["backend.env"].startswith(before["backend.env"])
        for name in ("runtime.env", "mongo.env"):
            assert after[name] == before[name]

    def test_a_line_somebody_set_is_left_as_it_is(self, older):
        path = older.settings.state / Settings.BACKEND
        path.write_text(path.read_text(encoding="utf-8") + "DEPLOYMENT_KIND=web\n",
                        encoding="utf-8", newline="\n")
        assert older.settings.bring_up_to_date() == []
        assert state_files(older)["backend.env"].count("DEPLOYMENT_KIND=") == 1
        assert "DEPLOYMENT_KIND=web\n" in state_files(older)["backend.env"]

    def test_an_install_that_says_it_is_told_nothing(self, installed):
        assert installed.settings.bring_up_to_date() == []


class TestAnUpdate:
    def test_it_copies_the_database_seeds_and_starts_the_new_release(
            self, installed, engine):
        said = installed.update(release("1.5.0"))
        assert said == "1.5.0 is installed."
        assert engine.pulled == ["registry/backend:1.5.0", "registry/runtime:1.5.0",
                                 "registry/frontend:1.5.0"]
        assert engine.stack() == [
            "up -d mongo", "stop backend ai-runtime caddy",
            "--profile seed run --rm init", "up -d"]
        # The copy was taken from the release that was running, and the
        # seeder that ran was the new one's.
        assert engine.environment_of("mongodump")["BACKEND_IMAGE"] == "registry/backend:1.4.0"
        assert engine.environment_of("run --rm init")["BACKEND_IMAGE"] == "registry/backend:1.5.0"
        install = installed.settings.install()
        assert install["version"] == "1.5.0"
        assert install["previous"]["version"] == "1.4.0"
        assert install["previous"]["images"]["backend"] == "registry/backend:1.4.0"
        [copy] = installed.settings.backups.glob("*.archive.gz")
        assert copy.read_bytes() == b"the database, as it was"
        assert copy.name.endswith("-1.4.0.archive.gz")

    def test_the_keys_and_the_settings_are_not_touched(self, installed):
        before = {name: text for name, text in state_files(installed).items()
                  if name.endswith(".env")}
        installed.update(release("1.5.0"))
        after = {name: text for name, text in state_files(installed).items()
                 if name.endswith(".env")}
        assert after == before

    def test_the_seeder_is_told_who_the_first_person_was_and_no_password(
            self, installed, engine):
        installed.update(release("1.5.0"))
        seeded = engine.environment_of("run --rm init")
        assert seeded["ADMIN_EMAIL"] == EMAIL
        assert "ADMIN_PASSWORD" not in seeded

    def test_a_release_that_is_not_newer_changes_nothing(self, installed, engine):
        assert "is not newer" in installed.update(release("1.4.0"))
        assert "is not newer" in installed.update(release("1.3.0"))
        assert engine.asked[1:] == [] or engine.stack() == []
        assert installed.settings.install()["version"] == "1.4.0"

    def test_it_can_be_installed_again_when_the_person_says_so(self, installed, engine):
        assert installed.update(release("1.4.0"), again=True) == "1.4.0 is installed."

    def test_a_release_that_does_not_come_up_is_taken_back(self, installed, engine):
        engine.states_with["registry/backend:1.5.0"] = {"backend": "running unhealthy"}
        with pytest.raises(InstallationError) as failed:
            installed.update(release("1.5.0"))
        assert "1.5.0 did not come up" in str(failed.value)
        assert "1.4.0 was put back as it was" in str(failed.value)
        # The copy went back into the database, and what runs is the
        # release from before.
        assert engine.restored == [b"the database, as it was"]
        assert engine.stack()[-3:] == ["stop backend ai-runtime caddy",
                                       "up -d mongo", "up -d"]
        assert engine.asked[-1]["environment"]["BACKEND_IMAGE"] == "registry/backend:1.4.0"
        install = installed.settings.install()
        assert install["version"] == "1.4.0" and install["previous"] is None

    def test_a_seeder_that_fails_is_taken_back_too(self, installed, engine):
        engine.fails["run --rm init"] = "the schema would not apply"
        with pytest.raises(InstallationError, match="the schema would not apply"):
            installed.update(release("1.5.0"))
        assert engine.restored == [b"the database, as it was"]
        assert installed.settings.install()["version"] == "1.4.0"

    def test_an_image_that_cannot_be_pulled_stops_it_before_anything_is_touched(
            self, installed, engine):
        engine.fails["pull registry/runtime:1.5.0"] = "manifest unknown"
        with pytest.raises(Exception, match="manifest unknown"):
            installed.update(release("1.5.0"))
        assert engine.stack() == []
        assert installed.settings.install()["version"] == "1.4.0"

    def test_the_last_three_copies_are_kept(self, installed):
        for version in ("1.5.0", "1.6.0", "1.7.0", "1.8.0", "1.9.0"):
            copy = installed.backup(version)
            copy.rename(copy.with_name(f"2026010{version[2]}T000000Z-{version}.archive.gz"))
        kept = sorted(p.name for p in installed.settings.backups.glob("*.archive.gz"))
        assert len(kept) <= Installation.BACKUPS_KEPT + 1
        assert installed.backup("2.0.0").is_file()
        kept = sorted(p.name for p in installed.settings.backups.glob("*.archive.gz"))
        assert len(kept) == Installation.BACKUPS_KEPT


class TestWhatIsInstalled:
    def test_the_record_is_written_whole(self, installed):
        path = installed.settings.state / Settings.INSTALL
        assert json.loads(path.read_text(encoding="utf-8"))["version"] == "1.4.0"
        assert not list(installed.settings.state.glob(".*.incoming"))


class TestAFolderToDevelopIn:
    """`develop`: a folder of the person's own, handed to the backend
    read-only as the one place agent sources may come from on this
    computer — kept across starts and updates until taken back."""

    FOLDER = "/run/desktop/mnt/host/c/Users/sara/agents"

    def test_it_is_handed_to_the_backend_alone(self, installed, engine):
        said = installed.develop(self.FOLDER)
        assert "address is /develop when that folder is your repository" in said
        assert engine.stack() == ["up -d backend"]
        override = installed.settings.develop_file
        assert override.is_file()
        up = next(asked for asked in engine.asked
                  if FakeEngine.after_files(asked["argv"])[:3] == ["up", "-d", "backend"])
        assert FakeEngine.files(up)[-1] == str(override)
        text = override.read_text(encoding="utf-8")
        assert f'source: "{self.FOLDER}"' in text
        assert "target: /develop" in text and "read_only: true" in text
        assert "AGENT_SOURCE_FOLDER: /develop" in text
        assert installed.settings.develop_folder == self.FOLDER
        assert installed.status()["develop_folder"] == self.FOLDER

    def test_it_stays_handed_over_across_a_start(self, installed, engine):
        installed.develop(self.FOLDER)
        engine.asked.clear()
        installed.start()
        up = next(asked for asked in engine.asked
                  if FakeEngine.after_files(asked["argv"])[:2] == ["up", "-d"])
        assert str(installed.settings.develop_file) in FakeEngine.files(up)

    def test_taken_back_it_is_gone(self, installed, engine):
        installed.develop(self.FOLDER)
        engine.asked.clear()
        said = installed.develop("")
        assert "addresses only" in said
        assert not installed.settings.develop_file.exists()
        assert installed.settings.develop_folder == ""
        ups = [asked for asked in engine.asked
               if FakeEngine.after_files(asked["argv"])[:3] == ["up", "-d", "backend"]]
        assert ups and FakeEngine.files(ups[-1]) == [str(installed.engine.compose_file)]

    def test_a_folder_the_engine_cannot_find_is_refused(self, installed):
        for written in (r"C:\Users\sara\agents", "agents", "/x\n/y"):
            with pytest.raises(SettingsError, match="as the engine sees it"):
                installed.develop(written)
        assert not installed.settings.develop_file.exists()

    def test_nothing_installed_nothing_handed_over(self, installation):
        with pytest.raises(InstallationError, match="not installed here yet"):
            installation.develop(self.FOLDER)


class TestANewPassword:
    """`reset-password`: what a reset link does, for an install with no
    email to send one — run by the platform's own script, in the
    backend's image, with the password said once and kept nowhere."""

    NEW = "another-passphrase-8"
    SCRIPT = "/opt/decentai/bootstrap/reset_password.py"

    def test_the_platforms_script_sets_it(self, installed, engine):
        said = installed.reset_password(EMAIL, self.NEW)
        assert said == f"The password of {EMAIL} is set. Sign in with it."
        assert engine.stack() == [
            "up -d mongo",
            f"--profile seed run --rm init python {self.SCRIPT}",
        ]
        environment = engine.environment_of("reset_password.py")
        assert environment["RESET_EMAIL"] == EMAIL
        assert environment["RESET_PASSWORD"] == self.NEW
        assert self.NEW not in json.dumps(state_files(installed))

    def test_the_first_person_when_nobody_is_named(self, installed, engine):
        installed.reset_password("", self.NEW)
        assert engine.environment_of("reset_password.py")["RESET_EMAIL"] == EMAIL

    def test_a_weak_password_is_refused_before_anything_runs(self, installed, engine):
        with pytest.raises(InstallationError, match="at least 10 characters"):
            installed.reset_password(EMAIL, "short1")
        assert engine.stack() == []

    def test_the_scripts_reason_is_what_the_person_is_told(self, installed, engine):
        engine.fails["reset_password.py"] = (
            "Creating decentai-app-init-run ... done\n"
            "ERROR: There is no account for nobody@example.com.")
        with pytest.raises(InstallationError) as refused:
            installed.reset_password("nobody@example.com", self.NEW)
        assert str(refused.value) == (
            "The password was not changed. "
            "There is no account for nobody@example.com.")

    def test_nothing_installed_nothing_to_reset(self, installation):
        with pytest.raises(InstallationError, match="not installed here yet"):
            installation.reset_password(EMAIL, self.NEW)
