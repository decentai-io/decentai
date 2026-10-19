"""The Data Core's settings by today's names, and by the names a
deploy.env from before the Data Core was named still uses
(contracts/setting_names.py)."""

import pytest

from server.setup.app_settings import _load_settings

PEM = "-----BEGIN PRIVATE KEY-----\\nkey\\n-----END PRIVATE KEY-----"


@pytest.fixture
def environment(monkeypatch):
    for name in ("DATA_CORE_SERVICE_PRIVATE_KEY", "DATA_CORE_PRIVATE_KEY",
                 "BACKEND_SERVICE_PRIVATE_KEY", "DATA_CORE_HOST",
                 "BACKEND_HOST", "DATA_CORE_PORT", "BACKEND_PORT"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


class TestTheDataCoresSettingNames:
    def test_todays_names_are_read(self, environment):
        environment.setenv("DATA_CORE_SERVICE_PRIVATE_KEY", PEM)
        environment.setenv("DATA_CORE_HOST", "0.0.0.0")
        environment.setenv("DATA_CORE_PORT", "8100")
        said = _load_settings()
        assert said["data_core_service_private_key"] == PEM.replace("\\n", "\n")
        assert (said["data_core_host"], said["data_core_port"]) == ("0.0.0.0", 8100)

    def test_an_install_from_before_is_read_by_its_own_names(self, environment):
        environment.setenv("BACKEND_SERVICE_PRIVATE_KEY", PEM)
        environment.setenv("BACKEND_HOST", "0.0.0.0")
        environment.setenv("BACKEND_PORT", "8100")
        said = _load_settings()
        assert said["data_core_service_private_key"] == PEM.replace("\\n", "\n")
        assert (said["data_core_host"], said["data_core_port"]) == ("0.0.0.0", 8100)

    def test_todays_name_wins_where_both_are_set(self, environment):
        environment.setenv("DATA_CORE_SERVICE_PRIVATE_KEY", "today")
        environment.setenv("DATA_CORE_PRIVATE_KEY", "earlier")
        environment.setenv("BACKEND_SERVICE_PRIVATE_KEY", "oldest")
        environment.setenv("DATA_CORE_PORT", "8100")
        environment.setenv("BACKEND_PORT", "8200")
        said = _load_settings()
        assert said["data_core_service_private_key"] == "today"
        assert said["data_core_port"] == 8100

    def test_nothing_set_is_the_default(self, environment):
        said = _load_settings()
        assert said["data_core_service_private_key"] is None
        assert (said["data_core_host"], said["data_core_port"]) == ("127.0.0.1", 8000)
