from app.core.settings import clear_settings_cache, get_settings


def test_formal_runtime_paths_use_garden_home(monkeypatch, tmp_path):
    from app.cli.paths import GardenPaths

    home = tmp_path / "garden-home"
    monkeypatch.setenv("GARDEN_HOME", str(home))

    paths = GardenPaths.from_environment()

    assert paths.home == home
    assert paths.config_file == home / "config.env"
    assert paths.database_file == home / "garden.db"
    assert paths.reports_dir == home / "reports"
    assert paths.logs_dir == home / "logs"
    assert paths.runtime_dir == home / "runtime"


def test_formal_runtime_reads_home_config_not_working_directory_dotenv(monkeypatch, tmp_path):
    home = tmp_path / "garden-home"
    home.mkdir()
    (home / "config.env").write_text("GARDEN_API_PORT=8123\n", encoding="utf-8")
    (tmp_path / ".env").write_text("GARDEN_API_PORT=9999\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GARDEN_HOME", str(home))
    monkeypatch.setenv("GARDEN_CLI_RUNTIME", "1")
    monkeypatch.delenv("GARDEN_DATABASE_URL", raising=False)
    clear_settings_cache()

    settings = get_settings()

    assert settings.api_port == 8123
    assert settings.database_url == f"sqlite+pysqlite:///{home / 'garden.db'}"
    clear_settings_cache()


def test_development_mode_keeps_current_directory_dotenv(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("GARDEN_API_PORT=9999\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GARDEN_HOME", raising=False)
    monkeypatch.delenv("GARDEN_CLI_RUNTIME", raising=False)
    clear_settings_cache()

    assert get_settings().api_port == 9999
    clear_settings_cache()
