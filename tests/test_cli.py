"""Tests de la CLI : aide, version, options globales, erreurs, `config show`."""

import logging
import re
from pathlib import Path

import pytest
import typer
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from githor import __version__, cli
from githor.config import GITHUB_TOKEN_ENV
from githor.errors import ConfigError
from githor.github.client import DEFAULT_API_URL
from githor.github.errors import AuthenticationError
from githor.github.token import ResolvedToken, TokenSource

runner = CliRunner()

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    """Retire les séquences ANSI ajoutées par Rich pour comparer du texte brut."""
    return ANSI.sub("", text)


@pytest.fixture
def app_with_command() -> typer.Typer:
    """Expose le callback réel derrière une sous-commande inoffensive.

    Les options globales ne prennent effet que lorsqu'une commande est invoquée ;
    tant que la V0.1 n'en déclare aucune, on en fournit une pour les tests.
    """
    app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
    app.callback()(cli.cli)

    @app.command("noop")
    def noop() -> None:
        """Commande de test sans effet."""

    return app


def test_help_exits_zero_and_lists_global_options() -> None:
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    output = plain(result.output)
    assert "githor" in output
    assert "--debug" in output
    assert "--version" in output


def test_version_prints_version_and_exits_zero() -> None:
    result = runner.invoke(cli.app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in plain(result.output)


def test_no_argument_shows_help_with_nonzero_exit() -> None:
    result = runner.invoke(cli.app, [])
    assert result.exit_code != 0
    assert "Usage" in plain(result.output)


def test_unknown_command_fails() -> None:
    result = runner.invoke(cli.app, ["commande-inexistante"])
    assert result.exit_code != 0


def test_debug_flag_sets_debug_level(app_with_command: typer.Typer) -> None:
    result = runner.invoke(app_with_command, ["--debug", "noop"])
    assert result.exit_code == 0
    assert logging.getLogger("githor").level == logging.DEBUG


def test_default_logging_stays_quiet(app_with_command: typer.Typer) -> None:
    result = runner.invoke(app_with_command, ["noop"])
    assert result.exit_code == 0
    assert logging.getLogger("githor").level == logging.WARNING


def test_main_reports_unexpected_error_without_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom() -> None:
        raise RuntimeError("boum")

    monkeypatch.setattr(cli, "app", boom)
    monkeypatch.setattr(cli.state, "debug", False)

    assert cli.main() == 1
    assert "boum" in plain(capsys.readouterr().err)


def test_main_reraises_in_debug_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise RuntimeError("boum")

    monkeypatch.setattr(cli, "app", boom)
    monkeypatch.setattr(cli.state, "debug", True)

    with pytest.raises(RuntimeError):
        cli.main()


def test_main_handles_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupted() -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "app", interrupted)
    assert cli.main() == 130


def test_main_reports_expected_errors_without_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Une GithorError est un message pour l'utilisateur, pas un incident."""

    def refuse() -> None:
        raise ConfigError("GITHUB_TOKEN n'est pas définie.")

    monkeypatch.setattr(cli, "app", refuse)
    monkeypatch.setattr(cli.state, "debug", False)

    assert cli.main() == 1
    err = plain(capsys.readouterr().err)
    assert "GITHUB_TOKEN" in err
    assert "inattendue" not in err


def test_config_show_displays_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli.app, ["config", "show"])
    output = plain(result.output)

    assert result.exit_code == 0
    assert "valeurs par défaut" in output
    assert "https://api.github.com" in output
    assert "absent" in output


def test_config_show_uses_explicit_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "perso.toml"
    path.write_text("[scan]\ncommit_history_days = 7\n", encoding="utf-8")

    result = runner.invoke(cli.app, ["--config", str(path), "config", "show"])

    assert result.exit_code == 0
    assert "7" in plain(result.output)


def test_config_show_never_prints_the_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(GITHUB_TOKEN_ENV, "ghp_ne_doit_pas_fuiter")

    result = runner.invoke(cli.app, ["config", "show"])
    output = plain(result.output)

    assert "ghp_ne_doit_pas_fuiter" not in output
    assert "configuré" in output


def test_config_show_reports_invalid_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli.app, ["--config", str(tmp_path / "absent.toml"), "config", "show"])

    assert result.exit_code != 0
    assert isinstance(result.exception, ConfigError)


# ── auth check ───────────────────────────────────────────────────────────────

USER_PAYLOAD = {"login": "nouhailler"}
RATE_LIMIT_PAYLOAD = {
    "resources": {"core": {"limit": 5000, "remaining": 4980, "used": 20, "reset": 4102444800}}
}


@pytest.fixture
def authenticated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Place un jeton dans l'environnement et isole le répertoire de travail."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(GITHUB_TOKEN_ENV, "ghp_jeton_de_test")


def test_auth_check_reports_user_and_quota(httpx_mock: HTTPXMock, authenticated: None) -> None:
    httpx_mock.add_response(url=f"{DEFAULT_API_URL}/user", json=USER_PAYLOAD)
    httpx_mock.add_response(url=f"{DEFAULT_API_URL}/rate_limit", json=RATE_LIMIT_PAYLOAD)

    result = runner.invoke(cli.app, ["auth", "check"])
    output = plain(result.output)

    assert result.exit_code == 0
    assert "nouhailler" in output
    assert "4980 / 5000" in output
    assert "OK" in output


def test_auth_check_never_prints_the_token(httpx_mock: HTTPXMock, authenticated: None) -> None:
    httpx_mock.add_response(url=f"{DEFAULT_API_URL}/user", json=USER_PAYLOAD)
    httpx_mock.add_response(url=f"{DEFAULT_API_URL}/rate_limit", json=RATE_LIMIT_PAYLOAD)

    result = runner.invoke(cli.app, ["auth", "check"])

    assert "ghp_jeton_de_test" not in plain(result.output)
    assert f"configuré ({GITHUB_TOKEN_ENV})" in plain(result.output)


def test_auth_check_sends_the_token_as_a_bearer(httpx_mock: HTTPXMock, authenticated: None) -> None:
    httpx_mock.add_response(url=f"{DEFAULT_API_URL}/user", json=USER_PAYLOAD)
    httpx_mock.add_response(url=f"{DEFAULT_API_URL}/rate_limit", json=RATE_LIMIT_PAYLOAD)

    runner.invoke(cli.app, ["auth", "check"])

    request = httpx_mock.get_request(url=f"{DEFAULT_API_URL}/user")
    assert request is not None
    assert request.headers["authorization"] == "Bearer ghp_jeton_de_test"


def test_auth_check_fails_without_any_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli.app, ["auth", "check"])

    assert result.exit_code != 0
    assert isinstance(result.exception, AuthenticationError)
    assert "gh auth login" in str(result.exception)


def test_auth_check_reports_an_invalid_token(httpx_mock: HTTPXMock, authenticated: None) -> None:
    httpx_mock.add_response(status_code=401, json={"message": "Bad credentials"})

    result = runner.invoke(cli.app, ["auth", "check"])

    assert result.exit_code != 0
    assert isinstance(result.exception, AuthenticationError)


def test_auth_check_warns_on_a_low_quota(httpx_mock: HTTPXMock, authenticated: None) -> None:
    low = {"resources": {"core": {"limit": 5000, "remaining": 40, "used": 4960, "reset": 0}}}
    httpx_mock.add_response(url=f"{DEFAULT_API_URL}/user", json=USER_PAYLOAD)
    httpx_mock.add_response(url=f"{DEFAULT_API_URL}/rate_limit", json=low)

    result = runner.invoke(cli.app, ["auth", "check"])
    output = plain(result.output)

    assert result.exit_code == 0
    assert "40 / 5000" in output
    assert "réinitialisation" in output


def test_auth_check_uses_the_configured_api_url(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(GITHUB_TOKEN_ENV, "ghp_jeton_de_test")
    config = tmp_path / "entreprise.toml"
    config.write_text('[github]\napi_url = "https://github.example.com/api/v3"\n', encoding="utf-8")

    httpx_mock.add_response(url="https://github.example.com/api/v3/user", json=USER_PAYLOAD)
    httpx_mock.add_response(
        url="https://github.example.com/api/v3/rate_limit", json=RATE_LIMIT_PAYLOAD
    )

    result = runner.invoke(cli.app, ["--config", str(config), "auth", "check"])

    assert result.exit_code == 0


def test_config_show_reports_the_token_source_from_gh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "find_token", lambda **_: ResolvedToken("x", TokenSource.GH_CLI))

    result = runner.invoke(cli.app, ["config", "show"])

    assert "gh CLI" in plain(result.output)


# ── repos ────────────────────────────────────────────────────────────────────

REPO_PAYLOAD = {
    "id": 1,
    "name": "Architecturor",
    "full_name": "nouhailler/Architecturor",
    "owner": {"login": "nouhailler"},
    "html_url": "https://github.com/nouhailler/Architecturor",
    "visibility": "public",
    "language": "TypeScript",
    "stargazers_count": 12,
    "open_issues_count": 4,
    "pushed_at": "2026-08-24T17:59:00Z",
}


def repo_payload(**overrides: object) -> dict[str, object]:
    """Construit une charge utile de repository pour les tests de la CLI."""
    return {**REPO_PAYLOAD, **overrides}


def test_repos_lists_repositories(httpx_mock: HTTPXMock, authenticated: None) -> None:
    httpx_mock.add_response(json=[repo_payload()])

    result = runner.invoke(cli.app, ["repos"])
    output = plain(result.output)

    assert result.exit_code == 0
    assert "nouhailler/Architecturor" in output
    assert "TypeScript" in output
    assert "2026-08-24" in output
    assert "1 repository(s) sur 1 accessibles" in output


def test_repos_excludes_forks_and_archived_by_default(
    httpx_mock: HTTPXMock, authenticated: None
) -> None:
    httpx_mock.add_response(
        json=[
            repo_payload(),
            repo_payload(id=2, name="f", full_name="nouhailler/f", fork=True),
            repo_payload(id=3, name="a", full_name="nouhailler/a", archived=True),
        ]
    )

    result = runner.invoke(cli.app, ["repos"])
    output = plain(result.output)

    assert "nouhailler/f" not in output
    assert "nouhailler/a" not in output
    assert "1 repository(s) sur 3 accessibles" in output
    assert "1 fork(s) et 1 archivé(s)" in output


def test_repos_can_include_forks(httpx_mock: HTTPXMock, authenticated: None) -> None:
    httpx_mock.add_response(
        json=[repo_payload(), repo_payload(id=2, name="f", full_name="nouhailler/f", fork=True)]
    )

    result = runner.invoke(cli.app, ["repos", "--include-forks"])

    assert "nouhailler/f" in plain(result.output)


def test_repos_can_include_archived(httpx_mock: HTTPXMock, authenticated: None) -> None:
    httpx_mock.add_response(
        json=[repo_payload(id=3, name="a", full_name="nouhailler/a", archived=True)]
    )

    result = runner.invoke(cli.app, ["repos", "--include-archived"])

    assert "nouhailler/a" in plain(result.output)


def test_repos_reports_an_empty_scope(httpx_mock: HTTPXMock, authenticated: None) -> None:
    httpx_mock.add_response(json=[])

    result = runner.invoke(cli.app, ["repos"])

    assert result.exit_code == 0
    assert "Aucun repository" in plain(result.output)


def test_repos_requires_a_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli.app, ["repos"])

    assert result.exit_code != 0
    assert isinstance(result.exception, AuthenticationError)


def test_repos_keeps_progress_off_stdout(httpx_mock: HTTPXMock, authenticated: None) -> None:
    """La progression part sur stderr : stdout reste exploitable en redirection."""
    httpx_mock.add_response(json=[repo_payload()])

    result = runner.invoke(cli.app, ["repos"])

    assert "Récupération des repositories" not in plain(result.stdout)


# ── db init ──────────────────────────────────────────────────────────────────


def test_db_init_creates_the_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli.app, ["db", "init"])
    output = plain(result.output)

    assert result.exit_code == 0
    assert (tmp_path / "data" / "githor.db").exists()
    assert "Base créée" in output
    assert "8 table(s)" in output
    assert "repository_snapshots" in output


def test_db_init_is_idempotent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    runner.invoke(cli.app, ["db", "init"])

    result = runner.invoke(cli.app, ["db", "init"])

    assert result.exit_code == 0
    assert "Base vérifiée" in plain(result.output)


def test_db_init_honours_the_configured_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "perso.toml"
    config.write_text('[storage]\ndatabase = "ailleurs/base.db"\n', encoding="utf-8")

    result = runner.invoke(cli.app, ["--config", str(config), "db", "init"])

    assert result.exit_code == 0
    assert (tmp_path / "ailleurs" / "base.db").exists()
