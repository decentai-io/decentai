"""bootstrap/setup.py — DecentAI on a person's own computer, from a
clone: the settings it writes, what it asks Docker for, and the address
that signs the first person in. No Docker runs here: something that
remembers what it was asked stands where the command line would."""

import base64
import importlib.util
import json
import re
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
            return 0, "sha256:the-backend-image\n"
        if "generate_service_keys.py" in words:
            return 0, (f"# backend/config.env\nBACKEND_SERVICE_PRIVATE_KEY={PRIVATE}\n\n"
                       f"# ai_runtime/config.env\nBACKEND_SERVICE_PUBLIC_KEY={PUBLIC}\n")
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
    shutil.copy(ROOT / "deploy.env.example", tmp_path / "deploy.env.example")
    said = []
    found = module.Setup(tmp_path, say=said.append)
    found.pause = 0
    found.UP_SECONDS = 1
    found.answers = lambda address: True
    found.said = said
    return found


def handed(link):
    """What the sign-in page reads out of the address, as it reads it."""
    found = re.search(r"#enter=([A-Za-z0-9_-]+)$", link)
    assert found, link
    padded = found.group(1) + "=" * (-len(found.group(1)) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


class TestTheFirstTime:
    def test_it_writes_the_settings_starts_the_stack_and_says_where(self, setup, docker):
        link = setup.run()
        assert link.startswith("http://localhost:4280/#enter=")
        assert docker.asked == [
            "compose version",
            "build -q -f backend/Dockerfile .",
            "run --rm sha256:the-backend-image python "
            "/opt/decentai/bootstrap/generate_service_keys.py",
            "run --rm sha256:the-backend-image python "
            "/opt/decentai/bootstrap/generate_secret_keys.py",
            "compose --env-file deploy.env up -d --build",
        ]

    def test_nothing_is_left_for_a_person_to_fill_in(self, setup):
        setup.run()
        said = setup.settings.values()
        assert not [name for name, value in said.items() if "change-me" in value]
        assert said["BACKEND_SERVICE_PRIVATE_KEY"] == PRIVATE
        assert said["BACKEND_SERVICE_PUBLIC_KEY"] == PUBLIC
        assert said["SECRET_ENCRYPTION_KEYS"] == "01:the-key"
        assert len(said["TOKEN_SECRET_KEY"]) >= 48

    def test_the_database_and_the_backend_agree_on_the_password(self, setup):
        setup.run()
        said = setup.settings.values()
        assert len(said["MONGO_ROOT_PASSWORD"]) >= 32
        assert said["MONGO_URI"] == (
            f"mongodb://{said['MONGO_ROOT_USERNAME']}:{said['MONGO_ROOT_PASSWORD']}"
            f"@mongo:27017/?authSource=admin")

    def test_it_is_one_persons_own_computer_in_plain_http(self, setup):
        setup.run()
        said = setup.settings.values()
        assert said["DEPLOYMENT_KIND"] == "desktop"
        assert said["LISTEN"] == "127.0.0.1" and said["SITE_ADDRESS"] == ":80"
        assert said["PUBLIC_APP_URL"] == said["CORS_ALLOW_ORIGINS"] == "http://localhost:4280"
        # A cookie marked for HTTPS alone would never be sent back.
        assert said["JWT_COOKIE_SECURE"] == "false"

    def test_another_port_is_said_everywhere_it_is_written(self, setup):
        link = setup.run(port=4300)
        said = setup.settings.values()
        assert said["PORT"] == "4300"
        assert said["PUBLIC_APP_URL"] == said["CORS_ALLOW_ORIGINS"] == "http://localhost:4300"
        assert link.startswith("http://localhost:4300/#enter=")

    def test_every_setting_of_the_template_is_still_there(self, setup):
        setup.run()
        template = module.Settings(setup.folder)
        template.path = setup.folder / "deploy.env.example"
        assert set(setup.settings.values()) == set(template.values())

    def test_the_first_person_is_nobodys_and_their_password_one_the_platform_takes(
            self, setup):
        setup.run()
        said = setup.settings.values()
        password = said["ADMIN_PASSWORD"]
        assert said["ADMIN_EMAIL"] == "me@decentai.local"
        assert len(password) >= 10 and password.strip() == password
        assert any(c.isalpha() for c in password) and any(c.isdigit() for c in password)

    def test_the_address_signs_that_person_in(self, setup):
        said_in_the_address = handed(setup.run())
        said = setup.settings.values()
        assert said_in_the_address == {"email": said["ADMIN_EMAIL"],
                                       "password": said["ADMIN_PASSWORD"]}

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
        template = setup.folder / "deploy.env.example"
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
                                "compose --env-file deploy.env up -d --build"]
        assert handed(link)["email"] == "me@decentai.local"

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


class TestAServersSettings:
    """A deploy.env written by hand for a server is somebody's own: it
    is not started as a computer of one's own, and it signs nobody in."""

    @pytest.fixture
    def server(self, setup):
        text = (setup.folder / "deploy.env.example").read_text(encoding="utf-8")
        (setup.folder / "deploy.env").write_text(
            text.replace("PUBLIC_APP_URL=http://localhost:4280",
                         "PUBLIC_APP_URL=https://ai.example.com"),
            encoding="utf-8")
        return setup

    def test_it_is_left_alone(self, server, docker):
        before = server.settings.path.read_text(encoding="utf-8")
        with pytest.raises(module.SetupError, match="is a server's"):
            server.run()
        assert server.settings.path.read_text(encoding="utf-8") == before
        assert docker.asked == ["compose version"]

    def test_it_gives_no_address_that_signs_in(self, server):
        with pytest.raises(module.SetupError, match="is a server's"):
            server.link()
