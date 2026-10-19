"""bootstrap/setup.py — DecentAI on a person's own computer, from a
clone: the settings it writes, what it asks Docker for, and the address
it says. No Docker runs here: something that remembers what it was
asked stands where the command line would."""

import importlib.util
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent

_spec = importlib.util.spec_from_file_location(
    "decentai_setup", ROOT / "bootstrap" / "setup.py")
module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(module)

PRIVATE = "-----BEGIN PRIVATE KEY-----\\nprivate\\n-----END PRIVATE KEY-----"
PUBLIC = "-----BEGIN PUBLIC KEY-----\\npublic\\n-----END PUBLIC KEY-----"


class FakeDocker:
    """Stands where Docker's command line would."""

    def __init__(self):
        self.asked = []
        self.fails = {}

    def __call__(self, argv, shown):
        self.asked.append(" ".join(argv[1:]))
        words = " ".join(argv)
        for fragment, why in self.fails.items():
            if fragment in words:
                return 1, why
        if "build -q" in words:
            return 0, "sha256:the-data-core-image\n"
        if "generate_service_keys.py" in words:
            return 0, (f"# the Data Core\nDATA_CORE_SERVICE_PRIVATE_KEY={PRIVATE}\n\n"
                       f"# every execution node\nDATA_CORE_SERVICE_PUBLIC_KEY={PUBLIC}\n")
        if "generate_node_authority.py" in words:
            return 0, (f"NODE_AUTHORITY_PRIVATE_KEY={PRIVATE}-of-the-authority\n\n"
                       f"NODE_AUTHORITY_PUBLIC_KEY={PUBLIC}-of-the-authority\n")
        if "generate_secret_keys.py" in words:
            return 0, "SECRET_ENCRYPTION_KEYS=01:the-key\nSECRET_ENCRYPTION_ACTIVE=01\n"
        return 0, ""


@pytest.fixture
def docker():
    fake = FakeDocker()
    module.Engine.runner = fake
    yield fake
    module.Engine.runner = None


@pytest.fixture
def setup(docker, tmp_path):
    (tmp_path / "config").mkdir()
    shutil.copy(ROOT / "config" / "deploy.env.example",
                tmp_path / "config" / "deploy.env.example")
    said = []
    found = module.Setup(tmp_path, say=said.append)
    found.pause = 0
    found.UP_SECONDS = 1
    found.answers = lambda address: True
    found.said = said
    return found


class TestTheFirstTime:
    def test_it_writes_the_settings_starts_the_stack_and_says_where(self, setup, docker):
        link = setup.run()
        assert link == "http://localhost:4280"
        assert docker.asked == [
            "compose version",
            "info --format {{.ServerVersion}}",
            "build -q -f data_core/Dockerfile .",
            "run --rm sha256:the-data-core-image python "
            "/opt/decentai/bootstrap/generate_service_keys.py",
            "run --rm sha256:the-data-core-image python "
            "/opt/decentai/bootstrap/generate_node_authority.py",
            "run --rm sha256:the-data-core-image python "
            "/opt/decentai/bootstrap/generate_secret_keys.py",
            "compose --env-file config/deploy.env up -d --build --remove-orphans",
        ]

    def test_nothing_is_left_for_a_person_to_fill_in(self, setup):
        setup.run()
        said = setup.settings.values()
        assert not [name for name, value in said.items() if "change-me" in value]
        assert said["DATA_CORE_SERVICE_PRIVATE_KEY"] == PRIVATE
        assert said["DATA_CORE_SERVICE_PUBLIC_KEY"] == PUBLIC
        # The install's own node authority: registered by the seeder,
        # signing the bundled node's credentials — nothing asked.
        assert said["NODE_AUTHORITY_PUBLIC_KEY"] == PUBLIC + "-of-the-authority"
        assert said["SECRET_ENCRYPTION_KEYS"] == "01:the-key"
        assert len(said["TOKEN_SECRET_KEY"]) >= 48

    def test_the_two_secrets_the_data_core_needs_not_are_kept_out_of_its_file(
            self, setup):
        """deploy.env is given whole to the Data Core and the seeder:
        the node's secret and the authority's private key are each in a
        file of their own, given only to what uses it."""
        setup.run()
        said = setup.settings.values()
        assert "AI_RUNTIME_SANDBOX_SECRET" not in said
        assert "NODE_AUTHORITY_PRIVATE_KEY" not in said
        sandbox = module.Settings._read(setup.folder / "config" / "sandbox.env")
        authority = module.Settings._read(setup.folder / "config" / "node-authority.env")
        assert set(sandbox) == {"AI_RUNTIME_SANDBOX_SECRET"}
        assert len(sandbox["AI_RUNTIME_SANDBOX_SECRET"]) >= 32
        assert authority == {"NODE_AUTHORITY_PRIVATE_KEY": PRIVATE + "-of-the-authority"}

    def test_the_secret_files_are_made_once(self, setup):
        setup.run()
        first = (setup.folder / "config" / "sandbox.env").read_text(encoding="utf-8")
        setup.run()
        assert (setup.folder / "config" / "sandbox.env").read_text(encoding="utf-8") == first

    def test_the_database_and_the_data_core_agree_on_the_password(self, setup):
        setup.run()
        said = setup.settings.values()
        assert len(said["MONGO_ROOT_PASSWORD"]) >= 32
        assert said["MONGO_URI"] == (
            f"mongodb://{said['MONGO_ROOT_USERNAME']}:{said['MONGO_ROOT_PASSWORD']}"
            f"@mongo:27017/?authSource=admin")

    def test_it_is_one_persons_own_computer_in_plain_http(self, setup):
        setup.run()
        said = setup.settings.values()
        assert said["LISTEN"] == "127.0.0.1" and said["SITE_ADDRESS"] == ":80"
        assert said["PUBLIC_APP_URL"] == said["CORS_ALLOW_ORIGINS"] == "http://localhost:4280"
        # A cookie marked for HTTPS alone would never be sent back.
        assert said["JWT_COOKIE_SECURE"] == "false"

    def test_another_port_is_said_everywhere_it_is_written(self, setup):
        link = setup.run(port=4300)
        said = setup.settings.values()
        assert said["PORT"] == "4300"
        assert said["PUBLIC_APP_URL"] == said["CORS_ALLOW_ORIGINS"] == "http://localhost:4300"
        assert link == "http://localhost:4300"

    def test_every_setting_of_the_template_is_still_there(self, setup):
        setup.run()
        template = module.Settings(setup.folder)
        template.path = setup.folder / "config" / "deploy.env.example"
        assert set(setup.settings.values()) == set(template.values())

    def test_nobody_is_made_for_the_person_at_the_keyboard(self, setup):
        """They name themselves on the first-run page: no address and
        no password is written down for anybody."""
        setup.run()
        said = setup.settings.values()
        assert said["ADMIN_EMAIL"] == said["ADMIN_PASSWORD"] == ""

    def test_the_first_run_page_asks_this_computer_for_no_code(self, setup):
        setup.run()
        assert setup.settings.values()["SETUP_CODE"] == "none"

    def test_the_address_carries_nothing(self, setup):
        link = setup.run()
        assert "#" not in link and "?" not in link

    def test_it_waits_until_decentai_answers(self, setup):
        looks = []

        def answers(address):
            looks.append(address)
            return len(looks) >= 3

        setup.answers = answers
        setup.run()
        assert looks == ["http://localhost:4280/healthz"] * 3

    def test_a_stack_that_never_answers_is_said_not_waited_for_for_ever(self, setup):
        setup.UP_SECONDS = 0
        setup.answers = lambda address: False
        with pytest.raises(module.SetupError, match="did not answer"):
            setup.run()


class TestWhatCanGoWrong:
    def test_no_docker(self, setup, docker):
        docker.fails["compose version"] = "docker: command not found"
        with pytest.raises(module.SetupError, match="Docker with Compose is needed"):
            setup.run()
        assert not setup.settings.written

    def test_docker_that_is_not_running(self, setup, docker):
        """Compose answers with no engine behind it; what is said is
        what to do, before anything is built."""
        docker.fails["info"] = "failed to connect to the docker API"
        with pytest.raises(module.SetupError, match="is not running"):
            setup.run()
        assert not setup.settings.written
        assert not any(asked.startswith("build") for asked in docker.asked)

    def test_an_image_that_does_not_build_writes_no_settings(self, setup, docker):
        docker.fails["build -q"] = "COPY failed"
        with pytest.raises(module.SetupError, match="could not be built"):
            setup.run()
        assert not setup.settings.written

    def test_a_key_the_platform_did_not_make_writes_no_settings(self, setup, docker):
        docker.fails["generate_secret_keys.py"] = "Traceback"
        with pytest.raises(module.SetupError, match="SECRET_ENCRYPTION_KEYS"):
            setup.run()
        assert not setup.settings.written

    def test_a_stack_that_does_not_start_keeps_its_settings(self, setup, docker):
        docker.fails["up -d"] = "port is already allocated"
        with pytest.raises(module.SetupError, match="did not start"):
            setup.run()
        # The keys are made: the next run carries on with them.
        assert setup.settings.written

    def test_a_template_that_is_not_the_one_it_was_written_for(self, setup):
        template = setup.folder / "config" / "deploy.env.example"
        template.write_text(
            template.read_text(encoding="utf-8").replace("TOKEN_SECRET_KEY=", "TOKEN="),
            encoding="utf-8")
        with pytest.raises(module.SetupError, match="names no TOKEN_SECRET_KEY"):
            setup.run()
        assert not setup.settings.written


class TestRunAgain:
    def test_the_keys_are_made_once(self, setup, docker):
        setup.run()
        before = setup.settings.path.read_text(encoding="utf-8")
        docker.asked.clear()
        link = setup.run()
        assert setup.settings.path.read_text(encoding="utf-8") == before
        assert docker.asked == ["compose version",
                                "info --format {{.ServerVersion}}",
                                "compose --env-file config/deploy.env up -d --build --remove-orphans"]
        assert link == "http://localhost:4280"

    @staticmethod
    def without(setup, prefix):
        """The file as an older platform wrote it: the lines naming
        ``prefix`` gone."""
        kept = setup.settings.path.read_text(encoding="utf-8")
        setup.settings.path.write_text(
            "\n".join(line for line in kept.splitlines()
                      if not line.startswith(prefix)) + "\n",
            encoding="utf-8")

    def test_a_key_the_platform_gained_is_made_and_added(self, setup, docker):
        """A deploy.env from before execution nodes were admitted by
        authorities: the run makes the install's authority and adds
        it, and the keys the file held are the keys it holds."""
        setup.run()
        before = setup.settings.values()
        self.without(setup, "NODE_AUTHORITY_")
        docker.asked.clear()
        setup.run()
        said = setup.settings.values()
        assert said["NODE_AUTHORITY_PUBLIC_KEY"] == PUBLIC + "-of-the-authority"
        assert "NODE_AUTHORITY_PRIVATE_KEY" not in said
        # The private half just made is beside it, in its own file.
        assert module.Settings._read(setup.folder / "config" / "node-authority.env") == {
            "NODE_AUTHORITY_PRIVATE_KEY": PRIVATE + "-of-the-authority"}
        kept = {name: value for name, value in before.items()
                if not name.startswith("NODE_AUTHORITY_")}
        assert {name: value for name, value in said.items()
                if not name.startswith("NODE_AUTHORITY_")} == kept
        assert docker.asked == [
            "compose version",
            "info --format {{.ServerVersion}}",
            "build -q -f data_core/Dockerfile .",
            "run --rm sha256:the-data-core-image python "
            "/opt/decentai/bootstrap/generate_node_authority.py",
            "compose --env-file config/deploy.env up -d --build --remove-orphans",
        ]
        assert any("gained NODE_AUTHORITY_PUBLIC_KEY" in line
                   for line in setup.said)

    def test_secrets_a_deploy_env_from_before_holds_are_moved_into_their_files(
            self, setup, docker):
        """A deploy.env written while it still held the node's secret
        and the authority's private key: they move into their own files
        as they were, and their lines say where they went."""
        setup.run()
        for name in ("sandbox.env", "node-authority.env"):
            (setup.folder / "config" / name).unlink()
        with setup.settings.path.open("a", encoding="utf-8") as older:
            older.write("AI_RUNTIME_SANDBOX_SECRET=the-older-secret\n"
                        f"NODE_AUTHORITY_PRIVATE_KEY={PRIVATE}-older\n")
        docker.asked.clear()
        setup.run()
        said = setup.settings.values()
        assert "AI_RUNTIME_SANDBOX_SECRET" not in said
        assert "NODE_AUTHORITY_PRIVATE_KEY" not in said
        text = setup.settings.path.read_text(encoding="utf-8")
        assert "# AI_RUNTIME_SANDBOX_SECRET moved to config/sandbox.env" in text
        assert "# NODE_AUTHORITY_PRIVATE_KEY moved to config/node-authority.env" in text
        assert module.Settings._read(setup.folder / "config" / "sandbox.env") == {
            "AI_RUNTIME_SANDBOX_SECRET": "the-older-secret"}
        assert module.Settings._read(setup.folder / "config" / "node-authority.env") == {
            "NODE_AUTHORITY_PRIVATE_KEY": PRIVATE + "-older"}
        # Nothing was made: no generator ran.
        assert not any("generate_" in asked for asked in docker.asked)

    def test_a_lost_authority_file_is_said_not_quietly_replaced(self, setup):
        setup.run()
        (setup.folder / "config" / "node-authority.env").unlink()
        with pytest.raises(module.SetupError, match="node-authority.env is missing"):
            setup.run()

    def test_a_setting_the_platform_gained_is_added_as_the_template_says(
            self, setup, docker):
        setup.run()
        self.without(setup, "JWT_COOKIE_SAMESITE=")
        docker.asked.clear()
        setup.run()
        text = setup.settings.path.read_text(encoding="utf-8")
        assert setup.settings.values()["JWT_COOKIE_SAMESITE"] == "lax"
        assert text.count("JWT_COOKIE_SAMESITE=") == 1
        assert "# Added by bootstrap/setup.py on " in text
        # No key was lacking, so no image is built and no generator run.
        assert docker.asked == ["compose version",
                                "info --format {{.ServerVersion}}",
                                "compose --env-file config/deploy.env up -d --build --remove-orphans"]

    def test_a_computers_file_from_before_the_first_run_page_asks_for_no_code(
            self, setup, docker):
        """The template's SETUP_CODE is a server's (empty: the
        deployment makes one); this computer's file gains "none"."""
        setup.run()
        self.without(setup, "SETUP_CODE=")
        setup.run()
        assert setup.settings.values()["SETUP_CODE"] == "none"

    def test_a_file_that_lacks_nothing_is_not_written(self, setup):
        setup.run()
        before = setup.settings.path.stat().st_mtime_ns
        setup.run()
        assert setup.settings.path.stat().st_mtime_ns == before

    def test_the_port_is_the_files_to_say_by_then(self, setup):
        setup.run()
        with pytest.raises(module.SetupError, match="says the port"):
            setup.run(port=4300)

    def test_the_address_is_said_without_starting_anything(self, setup, docker):
        first = setup.run()
        docker.asked.clear()
        assert setup.link() == first
        assert docker.asked == []

    def test_nothing_set_up_has_no_address(self, setup):
        with pytest.raises(module.SetupError, match="Nothing is set up here yet"):
            setup.link()


class TestAnInstallFromBeforeConfig:
    """An install from before config/ kept its three files at the
    repository's root. They are the install — its keys are made once —
    so they are moved into config/ as they are, and never over a file
    config/ already holds."""

    NAMES = ("deploy.env", "sandbox.env", "node-authority.env")

    @pytest.fixture
    def older(self, setup):
        """An install set up, and its files put back where an older
        checkout kept them."""
        setup.run()
        for name in self.NAMES:
            (setup.folder / "config" / name).replace(setup.folder / name)
        setup.said.clear()
        return setup

    def test_its_files_are_moved_into_config_as_they_are(self, older, docker):
        before = {name: (older.folder / name).read_bytes() for name in self.NAMES}
        docker.asked.clear()
        assert older.run() == "http://localhost:4280"
        for name in self.NAMES:
            assert not (older.folder / name).exists()
            assert (older.folder / "config" / name).read_bytes() == before[name]
        # Nothing is made again: the keys are the ones the files held.
        assert not any("generate_" in asked for asked in docker.asked)
        assert docker.asked[-1] == (
            "compose --env-file config/deploy.env up -d --build --remove-orphans")

    def test_the_move_is_said(self, older):
        older.run()
        assert any(line.startswith("Moved deploy.env, sandbox.env, "
                                   "node-authority.env from the repository's "
                                   "root into config/")
                   for line in older.said)

    def test_a_file_config_already_holds_is_never_overwritten(self, older):
        (older.folder / "config").mkdir(exist_ok=True)
        (older.folder / "deploy.env").replace(older.folder / "config" / "deploy.env")
        (older.folder / "deploy.env").write_text("PORT=1\n", encoding="utf-8")
        kept = (older.folder / "config" / "deploy.env").read_bytes()
        older.run()
        assert (older.folder / "config" / "deploy.env").read_bytes() == kept
        assert (older.folder / "deploy.env").read_text(encoding="utf-8") == "PORT=1\n"
        assert any(line.startswith("Left deploy.env at the repository's root")
                   for line in older.said)
        # The two the root alone held are moved all the same.
        assert not (older.folder / "sandbox.env").exists()
        assert (older.folder / "config" / "sandbox.env").is_file()

    def test_asking_for_the_address_moves_them_too(self, older, docker):
        docker.asked.clear()
        assert older.link() == "http://localhost:4280"
        assert (older.folder / "config" / "deploy.env").is_file()
        assert not (older.folder / "deploy.env").exists()
        assert docker.asked == []

    @staticmethod
    def named_as_before(folder):
        """The root's deploy.env as a checkout from before the engine's
        parts were named wrote it."""
        path = folder / "deploy.env"
        text = path.read_text(encoding="utf-8")
        for new, old in (
                ("DATA_CORE_SERVICE_PRIVATE_KEY=", "BACKEND_SERVICE_PRIVATE_KEY="),
                ("DATA_CORE_SERVICE_PUBLIC_KEY=", "BACKEND_SERVICE_PUBLIC_KEY="),
                ("DATA_CORE_INTERNAL_URL=http://data-core:8000",
                 "BACKEND_INTERNAL_URL=http://backend:8000"),
                ("# SANDBOX_MEMORY=2g", "# AGENTS_MEMORY=2g"),
                ("# SANDBOX_CPUS=2", "AGENTS_CPUS=1.5")):
            assert new in text
            text = text.replace(new, old)
        path.write_text(text, encoding="utf-8")

    def test_its_settings_are_renamed_and_their_values_kept(self, older, docker):
        self.named_as_before(older.folder)
        docker.asked.clear()
        older.run()
        said = older.settings.values()
        assert said["DATA_CORE_SERVICE_PRIVATE_KEY"] == PRIVATE
        assert said["DATA_CORE_SERVICE_PUBLIC_KEY"] == PUBLIC
        assert said["SANDBOX_CPUS"] == "1.5"
        assert not [name for name in said
                    if name.startswith(("BACKEND_", "AGENTS_"))]
        text = older.settings.path.read_text(encoding="utf-8")
        assert "# SANDBOX_MEMORY=2g" in text and "AGENTS_MEMORY" not in text
        # The keys are the ones the file held: nothing was made again.
        assert not any("generate_" in asked for asked in docker.asked)
        assert any("BACKEND_SERVICE_PRIVATE_KEY is DATA_CORE_SERVICE_PRIVATE_KEY"
                   in line for line in older.said)

    def test_an_address_that_named_the_old_service_names_the_new_one(self, older):
        self.named_as_before(older.folder)
        older.run()
        assert older.settings.values()["DATA_CORE_INTERNAL_URL"] == (
            "http://data-core:8000")

    def test_a_name_the_file_already_says_as_today_is_the_one_kept(self, setup):
        setup.run()
        with setup.settings.path.open("a", encoding="utf-8") as older:
            older.write("BACKEND_SERVICE_PRIVATE_KEY=an-older-key\n")
        setup.run()
        said = setup.settings.values()
        assert said["DATA_CORE_SERVICE_PRIVATE_KEY"] == PRIVATE
        assert said["BACKEND_SERVICE_PRIVATE_KEY"] == "an-older-key"

    def test_renaming_twice_changes_nothing_more(self, older):
        self.named_as_before(older.folder)
        older.run()
        before = older.settings.path.read_text(encoding="utf-8")
        older.said.clear()
        older.run()
        assert older.settings.path.read_text(encoding="utf-8") == before
        assert not any("names its settings" in line for line in older.said)

    def test_an_install_already_in_config_is_not_touched(self, setup):
        setup.run()
        before = setup.settings.path.stat().st_mtime_ns
        setup.said.clear()
        setup.run()
        assert setup.settings.path.stat().st_mtime_ns == before
        assert not any(line.startswith(("Moved", "Left")) for line in setup.said)


class TestAServersSettings:
    """A deploy.env written by hand for a server is somebody's own: it
    is not started as a computer of one's own, nor said as one."""

    @pytest.fixture
    def server(self, setup):
        text = (setup.folder / "config" / "deploy.env.example").read_text(encoding="utf-8")
        (setup.folder / "config" / "deploy.env").write_text(
            text.replace("PUBLIC_APP_URL=http://localhost:4280",
                         "PUBLIC_APP_URL=https://ai.example.com"),
            encoding="utf-8")
        return setup

    def test_it_is_left_alone(self, server, docker):
        before = server.settings.path.read_text(encoding="utf-8")
        with pytest.raises(module.SetupError, match="is a server's"):
            server.run()
        assert server.settings.path.read_text(encoding="utf-8") == before
        assert docker.asked == ["compose version", "info --format {{.ServerVersion}}"]

    def test_its_older_names_are_its_own_to_change(self, server):
        path = server.settings.path
        path.write_text(path.read_text(encoding="utf-8").replace(
            "DATA_CORE_SERVICE_PUBLIC_KEY=", "BACKEND_SERVICE_PUBLIC_KEY="),
            encoding="utf-8")
        before = path.read_text(encoding="utf-8")
        with pytest.raises(module.SetupError, match="is a server's"):
            server.run()
        assert path.read_text(encoding="utf-8") == before

    def test_it_gives_no_address_that_signs_in(self, server):
        with pytest.raises(module.SetupError, match="is a server's"):
            server.link()
