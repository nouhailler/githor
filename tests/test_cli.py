"""Tests de la CLI : aide, version, options globales, erreurs, `config show`."""

import csv
import io
import json
import logging
import re
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import typer
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from githor import __version__, cli
from githor.config import GITHUB_TOKEN_ENV
from githor.errors import ConfigError
from githor.github.client import DEFAULT_API_URL
from githor.github.errors import AuthenticationError, NotFoundError
from githor.github.token import ResolvedToken, TokenSource
from githor.models.finding import Finding, Severity, Status
from githor.models.repository import Repository
from githor.models.snapshot import RepositorySnapshot
from githor.storage.database import Database
from githor.storage.findings import save_findings
from githor.storage.repositories import add_snapshot, upsert_repository

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
    assert "16 table(s)" in output
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


# ── scan ─────────────────────────────────────────────────────────────────────


def mock_repository_list(
    httpx_mock: HTTPXMock, payloads: list[dict[str, object]], *, reusable: bool = False
) -> None:
    """Mocke la route de listage des dépôts."""
    httpx_mock.add_response(url=re.compile(r".*/user/repos.*"), json=payloads, is_reusable=reusable)


@pytest.fixture
def repository_details(httpx_mock: HTTPXMock) -> None:
    """Mocke les sous-ressources interrogées pour chaque dépôt lors d'un scan.

    Enregistrées après les réponses spécifiques d'un test, elles répondent à
    tout dépôt sans que chaque test ait à les décrire.
    """
    httpx_mock.add_response(
        url=re.compile(r".*/languages$"), json={"Python": 900, "CSS": 100}, is_reusable=True
    )
    httpx_mock.add_response(
        url=re.compile(r".*/git/trees/.*"),
        json={
            "truncated": False,
            "tree": [
                {"path": "README.md", "type": "blob", "size": 120},
                {"path": "src", "type": "tree"},
                {"path": "src/main.py", "type": "blob", "size": 400},
            ],
        },
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=re.compile(r".*/commits\?.*"),
        json=[
            {
                "sha": "a" * 40,
                "commit": {
                    "message": "Premier commit",
                    "author": {"name": "nouhailler", "date": "2026-09-01T10:00:00Z"},
                },
            }
        ],
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=re.compile(r".*/releases.*"),
        json=[
            {
                "tag_name": "v0.1.0",
                "name": "Première version",
                "published_at": "2026-08-01T09:00:00Z",
            }
        ],
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=re.compile(r".*/issues.*"),
        json=[
            {
                "id": 501,
                "number": 1,
                "title": "Documenter l'installation",
                "state": "open",
                "created_at": "2026-08-02T09:00:00Z",
            },
            # GitHub range les pull requests parmi les issues : elle doit être écartée.
            {
                "id": 502,
                "number": 2,
                "title": "Corriger le scan",
                "state": "open",
                "pull_request": {},
            },
        ],
        is_reusable=True,
    )


def snapshot_count(root: Path) -> int:
    """Compte les snapshots enregistrés dans la base du répertoire donné."""
    with sqlite3.connect(root / "data" / "githor.db") as connection:
        return int(connection.execute("SELECT COUNT(*) FROM repository_snapshots").fetchone()[0])


def test_scan_persists_repositories_and_snapshots(
    httpx_mock: HTTPXMock, authenticated: None, tmp_path: Path, repository_details: None
) -> None:
    mock_repository_list(httpx_mock, [repo_payload(), repo_payload(id=2, full_name="nouhailler/b")])

    result = runner.invoke(cli.app, ["scan"])
    output = plain(result.output)

    assert result.exit_code == 0
    assert "2 repository(s) scanné(s) : 2 nouveau(x), 0 mis à jour" in output
    assert "2 snapshot(s)" in output
    assert snapshot_count(tmp_path) == 2


def test_a_second_scan_adds_snapshots_without_duplicating_repositories(
    httpx_mock: HTTPXMock, authenticated: None, tmp_path: Path, repository_details: None
) -> None:
    """La propriété centrale : un scan ajoute une mesure, il n'écrase pas la précédente."""
    mock_repository_list(httpx_mock, [repo_payload()], reusable=True)

    runner.invoke(cli.app, ["scan"])
    result = runner.invoke(cli.app, ["scan"])
    output = plain(result.output)

    assert "1 repository(s) scanné(s) : 0 nouveau(x), 1 mis à jour" in output
    assert "snapshot 2" in output
    assert snapshot_count(tmp_path) == 2

    with sqlite3.connect(tmp_path / "data" / "githor.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM repositories").fetchone()[0] == 1


def test_scan_creates_the_schema_on_the_fly(
    httpx_mock: HTTPXMock, authenticated: None, tmp_path: Path, repository_details: None
) -> None:
    mock_repository_list(httpx_mock, [repo_payload()])

    result = runner.invoke(cli.app, ["scan"])

    assert result.exit_code == 0
    assert (tmp_path / "data" / "githor.db").exists()


def test_scan_applies_the_configured_scope(
    httpx_mock: HTTPXMock, authenticated: None, tmp_path: Path, repository_details: None
) -> None:
    mock_repository_list(
        httpx_mock, [repo_payload(), repo_payload(id=2, full_name="nouhailler/f", fork=True)]
    )

    result = runner.invoke(cli.app, ["scan"])

    assert "1 fork(s)" in plain(result.output)
    assert snapshot_count(tmp_path) == 1


def test_scan_of_a_single_repository_resolves_a_short_name(
    httpx_mock: HTTPXMock, authenticated: None, tmp_path: Path, repository_details: None
) -> None:
    httpx_mock.add_response(url=f"{DEFAULT_API_URL}/user", json={"login": "nouhailler"})
    httpx_mock.add_response(
        url=f"{DEFAULT_API_URL}/repos/nouhailler/Architecturor", json=repo_payload()
    )

    result = runner.invoke(cli.app, ["scan", "Architecturor"])

    assert result.exit_code == 0
    assert "1 repository(s) scanné(s)" in plain(result.output)
    assert snapshot_count(tmp_path) == 1


def test_scan_of_a_single_repository_accepts_a_full_name(
    httpx_mock: HTTPXMock, authenticated: None, tmp_path: Path, repository_details: None
) -> None:
    httpx_mock.add_response(
        url=f"{DEFAULT_API_URL}/repos/nouhailler/Architecturor", json=repo_payload()
    )

    result = runner.invoke(cli.app, ["scan", "nouhailler/Architecturor"])

    assert result.exit_code == 0
    assert snapshot_count(tmp_path) == 1


def test_scan_of_an_unknown_repository_fails(httpx_mock: HTTPXMock, authenticated: None) -> None:
    httpx_mock.add_response(url=f"{DEFAULT_API_URL}/user", json={"login": "nouhailler"})
    httpx_mock.add_response(status_code=404, json={"message": "Not Found"})

    result = runner.invoke(cli.app, ["scan", "NExistePas"])

    assert result.exit_code != 0
    assert isinstance(result.exception, NotFoundError)
    assert "Repository introuvable : NExistePas" in str(result.exception)


def test_scan_keeps_progress_off_stdout(
    httpx_mock: HTTPXMock, authenticated: None, repository_details: None
) -> None:
    mock_repository_list(httpx_mock, [repo_payload()])

    result = runner.invoke(cli.app, ["scan"])

    assert "Récupération des repositories" not in plain(result.stdout)


# ── Fraîcheur des snapshots ──────────────────────────────────────────────────


def backdate_snapshots(root: Path, moment: datetime) -> None:
    """Recule tous les snapshots enregistrés, pour simuler une mesure ancienne."""
    with sqlite3.connect(root / "data" / "githor.db") as connection:
        connection.execute(
            "UPDATE repository_snapshots SET collected_at = ?",
            (moment.strftime("%Y-%m-%d %H:%M:%S.%f"),),
        )


def test_a_recent_snapshot_spares_a_second_scan(
    httpx_mock: HTTPXMock, authenticated: None, tmp_path: Path, repository_details: None
) -> None:
    mock_repository_list(httpx_mock, [repo_payload()], reusable=True)

    runner.invoke(cli.app, ["scan"])
    result = runner.invoke(cli.app, ["scan", "--freshness", "24"])
    output = plain(result.output)

    assert result.exit_code == 0
    assert "ignoré" in output
    assert "0 repository(s) scanné(s)" in output
    assert "1 repository(s) ignoré(s)" in output
    # Un décompte de zéros n'apprendrait rien quand tout a été jugé frais.
    assert "snapshot(s)," not in output
    assert snapshot_count(tmp_path) == 1


def test_a_skipped_repository_costs_no_request(
    httpx_mock: HTTPXMock, authenticated: None, repository_details: None
) -> None:
    """Tout l'intérêt de la manœuvre : le quota GitHub n'est pas consommé."""
    mock_repository_list(httpx_mock, [repo_payload()], reusable=True)

    runner.invoke(cli.app, ["scan"])
    before = len(httpx_mock.get_requests())
    runner.invoke(cli.app, ["scan", "--freshness", "24"])
    sent = [request.url.path for request in httpx_mock.get_requests()[before:]]

    # Seul le listage des dépôts subsiste : il dit quels dépôts existent.
    assert sent == ["/user/repos"]


def test_the_skipped_line_says_how_old_the_measure_is(
    httpx_mock: HTTPXMock, authenticated: None, tmp_path: Path, repository_details: None
) -> None:
    mock_repository_list(httpx_mock, [repo_payload()], reusable=True)

    runner.invoke(cli.app, ["scan"])
    backdate_snapshots(tmp_path, datetime.now(UTC) - timedelta(hours=3))
    result = runner.invoke(cli.app, ["scan", "--freshness", "24"])

    assert "mesuré il y a 3 h" in plain(result.output)


def test_an_older_snapshot_is_measured_again(
    httpx_mock: HTTPXMock, authenticated: None, tmp_path: Path, repository_details: None
) -> None:
    mock_repository_list(httpx_mock, [repo_payload()], reusable=True)

    runner.invoke(cli.app, ["scan"])
    backdate_snapshots(tmp_path, datetime.now(UTC) - timedelta(hours=48))
    result = runner.invoke(cli.app, ["scan", "--freshness", "24"])
    output = plain(result.output)

    assert "1 repository(s) scanné(s)" in output
    assert "ignoré(s)" not in output
    assert snapshot_count(tmp_path) == 2


def test_a_repository_never_measured_is_always_scanned(
    httpx_mock: HTTPXMock, authenticated: None, tmp_path: Path, repository_details: None
) -> None:
    """La fraîcheur ne peut pas dispenser d'une mesure qui n'a jamais eu lieu."""
    mock_repository_list(httpx_mock, [repo_payload()])
    runner.invoke(cli.app, ["scan"])

    # Un second dépôt apparaît : lui n'a jamais été mesuré.
    mock_repository_list(httpx_mock, [repo_payload(), repo_payload(id=2, full_name="nouhailler/b")])
    result = runner.invoke(cli.app, ["scan", "--freshness", "24"])
    output = plain(result.output)

    assert "1 repository(s) scanné(s)" in output
    assert "1 repository(s) ignoré(s)" in output
    assert snapshot_count(tmp_path) == 2


def test_by_default_nothing_is_skipped(
    httpx_mock: HTTPXMock, authenticated: None, tmp_path: Path, repository_details: None
) -> None:
    """Sans consigne, un scan mesure tout : l'historique prime sur le quota."""
    mock_repository_list(httpx_mock, [repo_payload()], reusable=True)

    runner.invoke(cli.app, ["scan"])
    result = runner.invoke(cli.app, ["scan"])

    assert "ignoré" not in plain(result.output)
    assert snapshot_count(tmp_path) == 2


def test_freshness_can_be_configured(
    httpx_mock: HTTPXMock, authenticated: None, tmp_path: Path, repository_details: None
) -> None:
    config = tmp_path / "config" / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("[scan]\nsnapshot_freshness_hours = 24\n", encoding="utf-8")
    mock_repository_list(httpx_mock, [repo_payload()], reusable=True)

    runner.invoke(cli.app, ["scan"])
    result = runner.invoke(cli.app, ["scan"])

    assert "1 repository(s) ignoré(s)" in plain(result.output)
    assert snapshot_count(tmp_path) == 1


def test_the_option_overrides_the_configured_freshness(
    httpx_mock: HTTPXMock, authenticated: None, tmp_path: Path, repository_details: None
) -> None:
    """``--freshness 0`` doit pouvoir forcer une mesure malgré la configuration."""
    config = tmp_path / "config" / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("[scan]\nsnapshot_freshness_hours = 24\n", encoding="utf-8")
    mock_repository_list(httpx_mock, [repo_payload()], reusable=True)

    runner.invoke(cli.app, ["scan"])
    result = runner.invoke(cli.app, ["scan", "--freshness", "0"])

    assert "ignoré" not in plain(result.output)
    assert snapshot_count(tmp_path) == 2


def test_a_named_repository_also_honours_freshness(
    httpx_mock: HTTPXMock, authenticated: None, tmp_path: Path, repository_details: None
) -> None:
    httpx_mock.add_response(
        url=f"{DEFAULT_API_URL}/repos/nouhailler/Architecturor",
        json=repo_payload(),
        is_reusable=True,
    )

    runner.invoke(cli.app, ["scan", "nouhailler/Architecturor"])
    result = runner.invoke(cli.app, ["scan", "nouhailler/Architecturor", "--freshness", "24"])

    assert "1 repository(s) ignoré(s)" in plain(result.output)
    assert snapshot_count(tmp_path) == 1


def test_a_refused_freshness_is_reported(authenticated: None) -> None:
    result = runner.invoke(cli.app, ["scan", "--freshness", "-1"])

    assert result.exit_code != 0


# ── Findings (étape 10) ──────────────────────────────────────────────────────


def finding_rows(root: Path) -> list[tuple[str, str, str]]:
    """Retourne les constats enregistrés dans la base du répertoire donné."""
    with sqlite3.connect(root / "data" / "githor.db") as connection:
        return [
            (str(rule), str(severity), str(status))
            for rule, severity, status in connection.execute(
                "SELECT rule, severity, status FROM findings ORDER BY rule"
            )
        ]


def test_scan_evaluates_and_stores_findings(
    httpx_mock: HTTPXMock, authenticated: None, tmp_path: Path, repository_details: None
) -> None:
    mock_repository_list(httpx_mock, [repo_payload()])

    result = runner.invoke(cli.app, ["scan"])
    output = plain(result.output)
    rows = finding_rows(tmp_path)

    assert result.exit_code == 0
    assert "constat(s) évalué(s)" in output
    # L'arborescence mockée n'a qu'un README : la règle passe, les autres ouvrent.
    assert ("documentation.readme", "info", "ok") in rows
    assert ("documentation.changelog", "medium", "open") in rows


def test_findings_lists_every_scanned_repository(
    httpx_mock: HTTPXMock, authenticated: None, tmp_path: Path, repository_details: None
) -> None:
    mock_repository_list(httpx_mock, [repo_payload()])
    runner.invoke(cli.app, ["scan"])

    result = runner.invoke(cli.app, ["findings"])
    output = plain(result.output)

    assert result.exit_code == 0
    assert "nouhailler/Architecturor" in output
    assert "constat(s) ouvert(s)" in output


def test_findings_details_one_repository_from_a_short_name(
    httpx_mock: HTTPXMock, authenticated: None, tmp_path: Path, repository_details: None
) -> None:
    mock_repository_list(httpx_mock, [repo_payload()])
    runner.invoke(cli.app, ["scan"])

    result = runner.invoke(cli.app, ["findings", "architecturor"])
    output = plain(result.output)

    assert result.exit_code == 0
    assert "Documentation" in output
    assert "CHANGELOG" in output
    assert "Constats ouverts" in output
    assert "Ajouter un CHANGELOG.md" in output


def test_findings_never_calls_github(
    httpx_mock: HTTPXMock, authenticated: None, tmp_path: Path, repository_details: None
) -> None:
    """La lecture des constats se fait sur la base, pas sur l'API."""
    mock_repository_list(httpx_mock, [repo_payload()])
    runner.invoke(cli.app, ["scan"])
    httpx_mock.reset()

    result = runner.invoke(cli.app, ["findings"])

    assert result.exit_code == 0
    assert httpx_mock.get_requests() == []


def test_findings_of_an_unknown_repository_fails(authenticated: None, tmp_path: Path) -> None:
    runner.invoke(cli.app, ["db", "init"])

    result = runner.invoke(cli.app, ["findings", "NExistePas"])

    assert result.exit_code == 1
    assert "Repository inconnu" in plain(result.output)


def test_findings_without_a_database_explains_how_to_start(
    authenticated: None, tmp_path: Path
) -> None:
    result = runner.invoke(cli.app, ["findings"])

    assert result.exit_code == 0
    assert "githor scan" in plain(result.output)


def test_findings_presents_the_synthesis_in_catalog_order(
    httpx_mock: HTTPXMock, authenticated: None, tmp_path: Path, repository_details: None
) -> None:
    """La synthèse va du plus attendu au plus accessoire, comme le catalogue."""
    mock_repository_list(httpx_mock, [repo_payload()])
    runner.invoke(cli.app, ["scan"])

    output = plain(runner.invoke(cli.app, ["findings", "Architecturor"]).output)

    assert output.index("README") < output.index("LICENSE") < output.index("CHANGELOG")
    assert output.index("Documentation") < output.index("Maintenance")


# ── Compare (étape 22) ───────────────────────────────────────────────────────


def seed_two_repositories_for_compare(tmp_path: Path) -> None:
    """Enregistre deux dépôts aux constats distincts, pour comparer leurs scores."""
    moment = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    database = Database(tmp_path / "data" / "githor.db")
    database.create_schema()
    with database.session() as session:
        good, _ = upsert_repository(
            session,
            Repository(
                github_id=1,
                name="Astror",
                full_name="nouhailler/Astror",
                owner="nouhailler",
                html_url="https://github.com/nouhailler/Astror",
            ),
        )
        good_snapshot = add_snapshot(session, good.id, RepositorySnapshot(collected_at=moment))
        save_findings(
            session,
            good.id,
            good_snapshot.id,
            [
                Finding(
                    category="documentation",
                    rule="documentation.readme",
                    severity=Severity.INFO,
                    status=Status.OK,
                    message="README présent.",
                ),
                Finding(
                    category="development",
                    rule="development.tests",
                    severity=Severity.HIGH,
                    status=Status.OPEN,
                    message="tests/ absent.",
                    recommendation="Ajouter des tests.",
                ),
            ],
        )

        weak, _ = upsert_repository(
            session,
            Repository(
                github_id=2,
                name="Faible",
                full_name="nouhailler/Faible",
                owner="nouhailler",
                html_url="https://github.com/nouhailler/Faible",
            ),
        )
        weak_snapshot = add_snapshot(session, weak.id, RepositorySnapshot(collected_at=moment))
        save_findings(
            session,
            weak.id,
            weak_snapshot.id,
            [
                Finding(
                    category="documentation",
                    rule="documentation.readme",
                    severity=Severity.HIGH,
                    status=Status.OPEN,
                    message="README absent.",
                    recommendation="Ajouter un README.",
                ),
            ],
        )
    database.close()


def test_compare_ranks_repositories_by_overall_score(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    seed_two_repositories_for_compare(tmp_path)

    result = runner.invoke(cli.app, ["compare"])
    output = plain(result.output)

    assert result.exit_code == 0
    assert output.index("nouhailler/Astror") < output.index("nouhailler/Faible")
    assert "50 %" in output  # Astror : un finding sur deux satisfait
    assert "0 %" in output  # Faible : aucun
    assert "—" in output  # aucune règle CI/Security évaluée pour ces findings


def test_compare_accepts_a_single_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    seed_two_repositories_for_compare(tmp_path)

    result = runner.invoke(cli.app, ["compare", "Astror"])
    output = plain(result.output)

    assert result.exit_code == 0
    assert "nouhailler/Astror" in output
    assert "nouhailler/Faible" not in output


def test_compare_rejects_an_unknown_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    seed_two_repositories_for_compare(tmp_path)

    result = runner.invoke(cli.app, ["compare", "inconnu"])

    assert result.exit_code == 1
    assert "Repository inconnu" in plain(result.output)


def test_compare_without_a_database_says_what_to_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli.app, ["compare"])

    assert result.exit_code == 1
    assert "githor scan" in plain(result.output)


def test_compare_as_json_carries_the_score(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    seed_two_repositories_for_compare(tmp_path)

    result = runner.invoke(cli.app, ["compare", "Astror", "--format", "json"])
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["repositories"][0]["score"]["overall"] == 50


def test_compare_as_csv_carries_the_score(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    seed_two_repositories_for_compare(tmp_path)

    result = runner.invoke(cli.app, ["compare", "Astror", "--format", "csv"])
    rows = list(csv.DictReader(io.StringIO(result.output)))

    assert result.exit_code == 0
    assert rows[0]["score_overall"] == "50"


def test_compare_never_calls_github(
    httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    seed_two_repositories_for_compare(tmp_path)

    result = runner.invoke(cli.app, ["compare"])

    assert result.exit_code == 0
    assert httpx_mock.get_requests() == []


# ── Export (étape 11) ────────────────────────────────────────────────────────


def exported_files(root: Path, suffix: str) -> list[Path]:
    """Retourne les exports d'un format donné écrits sous le répertoire de travail."""
    return sorted((root / "data" / "exports").glob(f"githor-*{suffix}"))


def test_export_writes_a_json_file(
    httpx_mock: HTTPXMock, authenticated: None, tmp_path: Path, repository_details: None
) -> None:
    mock_repository_list(httpx_mock, [repo_payload()])
    runner.invoke(cli.app, ["scan"])

    result = runner.invoke(cli.app, ["export", "--format", "json"])
    files = exported_files(tmp_path, ".json")

    assert result.exit_code == 0
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["repository_count"] == 1
    assert payload["repositories"][0]["full_name"] == "nouhailler/Architecturor"


def test_export_defaults_to_json(
    httpx_mock: HTTPXMock, authenticated: None, tmp_path: Path, repository_details: None
) -> None:
    mock_repository_list(httpx_mock, [repo_payload()])
    runner.invoke(cli.app, ["scan"])

    result = runner.invoke(cli.app, ["export"])

    assert result.exit_code == 0
    assert exported_files(tmp_path, ".json")


def test_export_writes_csv_and_markdown(
    httpx_mock: HTTPXMock, authenticated: None, tmp_path: Path, repository_details: None
) -> None:
    mock_repository_list(httpx_mock, [repo_payload()])
    runner.invoke(cli.app, ["scan"])

    runner.invoke(cli.app, ["export", "-f", "csv"])
    runner.invoke(cli.app, ["export", "-f", "markdown"])

    assert exported_files(tmp_path, ".csv")
    assert (
        exported_files(tmp_path, ".md")[0]
        .read_text(encoding="utf-8")
        .startswith("# Inventaire Githor")
    )


def test_export_honours_the_output_option(
    httpx_mock: HTTPXMock, authenticated: None, tmp_path: Path, repository_details: None
) -> None:
    mock_repository_list(httpx_mock, [repo_payload()])
    runner.invoke(cli.app, ["scan"])
    destination = tmp_path / "ailleurs"

    result = runner.invoke(cli.app, ["export", "--output", str(destination)])

    assert result.exit_code == 0
    assert list(destination.glob("githor-*.json"))


def test_export_never_calls_github(
    httpx_mock: HTTPXMock, authenticated: None, tmp_path: Path, repository_details: None
) -> None:
    """Un export décrit le dernier scan ; il ne réinterroge pas GitHub."""
    mock_repository_list(httpx_mock, [repo_payload()])
    runner.invoke(cli.app, ["scan"])
    httpx_mock.reset()

    result = runner.invoke(cli.app, ["export"])

    assert result.exit_code == 0
    assert httpx_mock.get_requests() == []


def test_export_rejects_an_unknown_format(authenticated: None) -> None:
    result = runner.invoke(cli.app, ["export", "--format", "yaml"])

    assert result.exit_code != 0


def test_export_without_a_database_explains_how_to_start(
    authenticated: None, tmp_path: Path
) -> None:
    result = runner.invoke(cli.app, ["export"])

    assert result.exit_code == 0
    assert "githor scan" in plain(result.output)
    assert not exported_files(tmp_path, ".json")


def test_export_of_an_empty_database_writes_nothing(authenticated: None, tmp_path: Path) -> None:
    runner.invoke(cli.app, ["db", "init"])

    result = runner.invoke(cli.app, ["export"])

    assert result.exit_code == 0
    assert "Aucun repository enregistré" in plain(result.output)
    assert not exported_files(tmp_path, ".json")


# ── Rapports (étape 12) ──────────────────────────────────────────────────────


def test_report_writes_markdown_on_stdout(
    httpx_mock: HTTPXMock, authenticated: None, tmp_path: Path, repository_details: None
) -> None:
    mock_repository_list(httpx_mock, [repo_payload()])
    runner.invoke(cli.app, ["scan"])

    result = runner.invoke(cli.app, ["report", "Architecturor"])

    assert result.exit_code == 0
    assert result.stdout.startswith("# nouhailler/Architecturor\n")
    assert "## Vue d'ensemble" in result.stdout


def test_report_accepts_a_full_name(
    httpx_mock: HTTPXMock, authenticated: None, tmp_path: Path, repository_details: None
) -> None:
    mock_repository_list(httpx_mock, [repo_payload()])
    runner.invoke(cli.app, ["scan"])

    result = runner.invoke(cli.app, ["report", "nouhailler/Architecturor"])

    assert result.exit_code == 0
    assert "# nouhailler/Architecturor" in result.stdout


def test_report_keeps_tables_intact_on_a_narrow_terminal(
    httpx_mock: HTTPXMock, authenticated: None, tmp_path: Path, repository_details: None
) -> None:
    """Le Markdown part tel quel sur stdout : Rich le replierait et casserait les tableaux."""
    mock_repository_list(httpx_mock, [repo_payload()])
    runner.invoke(cli.app, ["scan"])

    result = runner.invoke(cli.app, ["report", "Architecturor"], terminal_width=40)

    assert "| Metric | Value |" in result.stdout


def test_report_writes_a_file_when_asked(
    httpx_mock: HTTPXMock, authenticated: None, tmp_path: Path, repository_details: None
) -> None:
    mock_repository_list(httpx_mock, [repo_payload()])
    runner.invoke(cli.app, ["scan"])
    destination = tmp_path / "rapport.md"

    result = runner.invoke(cli.app, ["report", "Architecturor", "-o", str(destination)])

    assert result.exit_code == 0
    assert destination.read_text(encoding="utf-8").startswith("# nouhailler/Architecturor")
    assert "# nouhailler/Architecturor" not in plain(result.output)


def test_report_writes_a_timestamped_file_into_a_directory(
    httpx_mock: HTTPXMock, authenticated: None, tmp_path: Path, repository_details: None
) -> None:
    mock_repository_list(httpx_mock, [repo_payload()])
    runner.invoke(cli.app, ["scan"])
    destination = tmp_path / "rapports"
    destination.mkdir()

    result = runner.invoke(cli.app, ["report", "Architecturor", "--output", str(destination)])

    assert result.exit_code == 0
    assert list(destination.glob("githor-report-nouhailler-Architecturor-*.md"))


def test_report_never_calls_github(
    httpx_mock: HTTPXMock, authenticated: None, tmp_path: Path, repository_details: None
) -> None:
    """Un rapport relit la base : il ne dépend ni du réseau ni du quota."""
    mock_repository_list(httpx_mock, [repo_payload()])
    runner.invoke(cli.app, ["scan"])
    httpx_mock.reset()

    result = runner.invoke(cli.app, ["report", "Architecturor"])

    assert result.exit_code == 0
    assert httpx_mock.get_requests() == []


def test_report_of_an_unknown_repository_fails(authenticated: None, tmp_path: Path) -> None:
    runner.invoke(cli.app, ["db", "init"])

    result = runner.invoke(cli.app, ["report", "Inexistant"])

    assert result.exit_code == 1
    assert "Repository inconnu" in plain(result.output)


def test_report_without_a_database_explains_how_to_start(
    authenticated: None, tmp_path: Path
) -> None:
    result = runner.invoke(cli.app, ["report", "Architecturor"])

    assert result.exit_code == 1
    assert "githor scan" in plain(result.output)


# ── githor mirror ────────────────────────────────────────────────────────────


def git(*arguments: str, cwd: Path) -> None:
    """Lance une commande git dans un dépôt de test."""
    subprocess.run(("git", *arguments), cwd=cwd, capture_output=True, text=True, check=True)


@pytest.fixture
def remote_repository(tmp_path: Path) -> Path:
    """Crée un dépôt git local servant d'origine au miroir."""
    origin = tmp_path / "origine"
    origin.mkdir()
    git("init", "--quiet", "--initial-branch", "main", cwd=origin)
    git("config", "user.email", "test@githor.local", cwd=origin)
    git("config", "user.name", "Test Githor", cwd=origin)
    (origin / "module.py").write_text("VALEUR = 1\n", encoding="utf-8")
    git("add", ".", cwd=origin)
    git("commit", "--quiet", "-m", "Premier commit", cwd=origin)
    return origin


@pytest.fixture
def database_with_repository(authenticated: None, tmp_path: Path, remote_repository: Path) -> Path:
    """Enregistre en base un dépôt dont l'URL désigne l'origine locale."""
    database = Database(tmp_path / "data" / "githor.db")
    database.create_schema()
    with database.session() as session:
        upsert_repository(
            session,
            Repository(
                github_id=1,
                name="depot",
                full_name="proprio/depot",
                owner="proprio",
                html_url=f"file://{remote_repository}",
                default_branch="main",
            ),
        )
    database.close()
    return tmp_path


def test_mirror_clones_a_registered_repository(database_with_repository: Path) -> None:
    result = runner.invoke(cli.app, ["mirror"])
    output = plain(result.output)

    assert result.exit_code == 0
    assert "proprio/depot" in output
    assert "1 miroir(s) à jour : 1 cloné(s), 0 relu(s)" in output
    assert (
        database_with_repository / "data" / "repos" / "proprio" / "depot" / "module.py"
    ).exists()


def test_a_second_mirror_updates_instead_of_cloning(database_with_repository: Path) -> None:
    runner.invoke(cli.app, ["mirror"])

    result = runner.invoke(cli.app, ["mirror"])
    output = plain(result.output)

    assert result.exit_code == 0
    assert "0 cloné(s), 1 relu(s)" in output


def test_mirror_reports_the_analysed_commit(
    database_with_repository: Path, remote_repository: Path
) -> None:
    """Le miroir doit dire sur quel état il est, sans quoi une analyse est indatable."""
    head = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=remote_repository,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    output = plain(runner.invoke(cli.app, ["mirror"]).output)

    assert head[:7] in output


def test_mirror_accepts_a_single_repository(database_with_repository: Path) -> None:
    result = runner.invoke(cli.app, ["mirror", "depot"])

    assert result.exit_code == 0
    assert "1 miroir(s) à jour" in plain(result.output)


def test_mirror_rejects_an_unknown_repository(database_with_repository: Path) -> None:
    result = runner.invoke(cli.app, ["mirror", "inconnu"])

    assert result.exit_code == 1
    assert "Repository inconnu" in plain(result.output)


def test_mirror_without_a_database_says_what_to_run(authenticated: None, tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["mirror"])

    assert result.exit_code == 1
    assert "githor scan" in plain(result.output)


def test_offline_mirror_never_contacts_the_origin(
    database_with_repository: Path, remote_repository: Path
) -> None:
    runner.invoke(cli.app, ["mirror"])
    (remote_repository / "module.py").write_text("VALEUR = 2\n", encoding="utf-8")
    git("commit", "--quiet", "-am", "Non récupéré", cwd=remote_repository)

    result = runner.invoke(cli.app, ["mirror", "--offline"])
    mirrored = database_with_repository / "data" / "repos" / "proprio" / "depot" / "module.py"

    assert result.exit_code == 0
    assert "hors ligne" in plain(result.output)
    assert mirrored.read_text() == "VALEUR = 1\n"


def test_a_failing_repository_is_reported_without_stopping_the_others(
    authenticated: None, tmp_path: Path, remote_repository: Path
) -> None:
    """Un dépôt injoignable ne doit pas empêcher de cloner les suivants."""
    database = Database(tmp_path / "data" / "githor.db")
    database.create_schema()
    with database.session() as session:
        upsert_repository(
            session,
            Repository(
                github_id=1,
                name="absent",
                full_name="proprio/absent",
                owner="proprio",
                html_url=f"file://{tmp_path / 'nulle-part'}",
                default_branch="main",
            ),
        )
        upsert_repository(
            session,
            Repository(
                github_id=2,
                name="depot",
                full_name="proprio/depot",
                owner="proprio",
                html_url=f"file://{remote_repository}",
                default_branch="main",
            ),
        )
    database.close()

    result = runner.invoke(cli.app, ["mirror"])
    output = plain(result.output)

    assert result.exit_code == 1
    assert "1 dépôt(s) en échec" in output
    assert "1 miroir(s) à jour" in output
    assert (tmp_path / "data" / "repos" / "proprio" / "depot" / "module.py").exists()


def test_mirror_does_not_call_github(database_with_repository: Path) -> None:
    """La commande relit la base : aucun quota ne doit être consommé.

    Aucune réponse HTTP n'est enregistrée ; pytest-httpx échouerait sur toute
    requête réellement émise.
    """
    assert runner.invoke(cli.app, ["mirror"]).exit_code == 0


# ── githor audit ─────────────────────────────────────────────────────────────


@pytest.fixture
def remote_with_code(remote_repository: Path) -> Path:
    """Ajoute à l'origine locale du code Python analysable."""
    source = remote_repository / "src" / "paquet"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text('"""Paquet."""\n', encoding="utf-8")
    (source / "app.py").write_text(
        '"""Module."""\n'
        "import os\n"
        "import httpx\n"
        "\n"
        "\n"
        "class Service:\n"
        "    def traiter(self, valeur):\n"
        "        if valeur:\n"
        "            return 1\n"
        "        return 0\n",
        encoding="utf-8",
    )
    (remote_repository / "app.ts").write_text("const a = 1;\n", encoding="utf-8")
    git("add", ".", cwd=remote_repository)
    git("commit", "--quiet", "-m", "Du code", cwd=remote_repository)
    return remote_repository


def test_audit_analyses_a_single_repository(
    database_with_repository: Path, remote_with_code: Path
) -> None:
    result = runner.invoke(cli.app, ["audit", "depot"])
    output = plain(result.output)

    assert result.exit_code == 0
    assert "proprio/depot" in output
    assert "Python" in output
    assert "TypeScript" in output
    assert "1 dépôt(s) analysé(s)" in output


def test_audit_reports_structure_and_complexity(
    database_with_repository: Path, remote_with_code: Path
) -> None:
    output = plain(runner.invoke(cli.app, ["audit", "depot"]).output)

    assert "Service.traiter" in output
    assert "1 fonction(s), 1 classe(s)" in output


def test_audit_names_third_party_imports(
    database_with_repository: Path, remote_with_code: Path
) -> None:
    """« os » vient de la bibliothèque standard, « httpx » doit être installé."""
    output = plain(runner.invoke(cli.app, ["audit", "depot"]).output)

    assert "Imports tierce partie : httpx" in output


def test_audit_clones_the_repository_when_needed(
    database_with_repository: Path, remote_with_code: Path
) -> None:
    assert runner.invoke(cli.app, ["audit"]).exit_code == 0
    assert (database_with_repository / "data" / "repos" / "proprio" / "depot").is_dir()


def test_audit_works_offline_on_an_existing_mirror(
    database_with_repository: Path, remote_with_code: Path
) -> None:
    runner.invoke(cli.app, ["mirror"])

    result = runner.invoke(cli.app, ["audit", "--offline"])

    assert result.exit_code == 0
    assert "1 dépôt(s) analysé(s)" in plain(result.output)


def test_audit_offline_without_a_mirror_fails_clearly(
    database_with_repository: Path, remote_with_code: Path
) -> None:
    result = runner.invoke(cli.app, ["audit", "--offline"])

    assert result.exit_code == 1
    assert "1 dépôt(s) en échec" in plain(result.output)


def test_audit_rejects_an_unknown_repository(database_with_repository: Path) -> None:
    result = runner.invoke(cli.app, ["audit", "inconnu"])

    assert result.exit_code == 1
    assert "Repository inconnu" in plain(result.output)


def test_audit_without_a_database_says_what_to_run(authenticated: None, tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["audit"])

    assert result.exit_code == 1
    assert "githor scan" in plain(result.output)


def test_audit_does_not_call_github(database_with_repository: Path, remote_with_code: Path) -> None:
    """Aucune réponse HTTP n'est enregistrée : une vraie requête ferait échouer le test."""
    assert runner.invoke(cli.app, ["audit"]).exit_code == 0


def test_audit_reports_declared_dependencies(
    database_with_repository: Path, remote_with_code: Path
) -> None:
    (remote_with_code / "pyproject.toml").write_text(
        '[project]\nname = "depot"\ndependencies = ["httpx>=0.27"]\n'
        '[project.optional-dependencies]\ndev = ["pytest>=8"]\n',
        encoding="utf-8",
    )
    git("add", ".", cwd=remote_with_code)
    git("commit", "--quiet", "-m", "Manifeste", cwd=remote_with_code)

    output = plain(runner.invoke(cli.app, ["audit", "depot"]).output)

    assert "Dépendances déclarées" in output
    assert "httpx" in output
    assert "pyproject.toml" in output
    assert "1 exécution, 1 optionnelle(s)" in output


def test_audit_reports_the_test_suite(
    database_with_repository: Path, remote_with_code: Path
) -> None:
    tests_dir = remote_with_code / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_app.py").write_text(
        "import pytest\n\n\ndef test_traiter():\n    pass\n", encoding="utf-8"
    )
    git("add", ".", cwd=remote_with_code)
    git("commit", "--quiet", "-m", "Des tests", cwd=remote_with_code)

    output = plain(runner.invoke(cli.app, ["audit", "depot"]).output)

    assert "1 fichier(s) · 1 fonction(s) · pytest" in output


def test_audit_says_when_a_repository_has_no_tests(
    database_with_repository: Path, remote_with_code: Path
) -> None:
    output = plain(runner.invoke(cli.app, ["audit", "depot"]).output)

    assert "aucun fichier de test" in output


def test_audit_persists_its_findings(
    database_with_repository: Path, remote_with_code: Path
) -> None:
    result = runner.invoke(cli.app, ["audit", "depot"])

    with sqlite3.connect(database_with_repository / "data" / "githor.db") as connection:
        audits = connection.execute("SELECT COUNT(*) FROM code_audits").fetchone()[0]
        modules = connection.execute("SELECT COUNT(*) FROM code_modules").fetchone()[0]
        functions = connection.execute("SELECT COUNT(*) FROM code_functions").fetchone()[0]

    assert result.exit_code == 0
    assert audits == 1
    assert modules > 0
    assert functions > 0
    assert "audit 1" in plain(result.output)


def test_a_second_audit_is_added_not_replaced(
    database_with_repository: Path, remote_with_code: Path
) -> None:
    """Deux analyses successives se comparent : elles ne s'écrasent pas."""
    runner.invoke(cli.app, ["audit", "depot"])
    result = runner.invoke(cli.app, ["audit", "depot"])

    with sqlite3.connect(database_with_repository / "data" / "githor.db") as connection:
        audits = connection.execute("SELECT COUNT(*) FROM code_audits").fetchone()[0]

    assert audits == 2
    assert "audit 2" in plain(result.output)


def test_no_save_writes_nothing(database_with_repository: Path, remote_with_code: Path) -> None:
    result = runner.invoke(cli.app, ["audit", "depot", "--no-save"])

    with sqlite3.connect(database_with_repository / "data" / "githor.db") as connection:
        audits = connection.execute("SELECT COUNT(*) FROM code_audits").fetchone()[0]

    assert result.exit_code == 0
    assert audits == 0
    assert "rien n'a été écrit en base" in plain(result.output)


def test_a_report_carries_the_audit_after_it_ran(
    database_with_repository: Path, remote_with_code: Path
) -> None:
    """L'audit et le rapport doivent décrire le même dépôt sans se contredire."""
    runner.invoke(cli.app, ["audit", "depot"])

    output = runner.invoke(cli.app, ["report", "depot"]).output

    assert "## Code" in output
    assert "| Functions | 1 |" in output


# ── githor advise (étape 27) ─────────────────────────────────────────────────

OLLAMA_GENERATE_URL = "http://localhost:11434/api/generate"

OPEN_FINDING = Finding(
    category="development",
    rule="development.tests",
    severity=Severity.HIGH,
    status=Status.OPEN,
    message="tests/ absent.",
    recommendation="Ajouter un répertoire de tests.",
)

OK_FINDING = Finding(
    category="documentation",
    rule="documentation.readme",
    severity=Severity.INFO,
    status=Status.OK,
    message="README présent : README.md.",
)


def seed_repository_with_findings(tmp_path: Path, *, findings: list[Finding]) -> None:
    """Enregistre un dépôt, un snapshot et ses constats — sans passer par un scan."""
    database = Database(tmp_path / "data" / "githor.db")
    database.create_schema()
    with database.session() as session:
        row, _ = upsert_repository(
            session,
            Repository(
                github_id=1,
                name="Architecturor",
                full_name="nouhailler/Architecturor",
                owner="nouhailler",
                html_url="https://github.com/nouhailler/Architecturor",
            ),
        )
        snapshot = add_snapshot(
            session,
            row.id,
            RepositorySnapshot(collected_at=datetime(2026, 9, 8, 12, 0, tzinfo=UTC)),
        )
        save_findings(session, row.id, snapshot.id, findings)
    database.close()


def mock_advice(
    httpx_mock: HTTPXMock, items: list[dict[str, str]], *, reusable: bool = False
) -> None:
    """Simule la réponse d'Ollama à /api/generate."""
    httpx_mock.add_response(
        url=OLLAMA_GENERATE_URL, json={"response": json.dumps(items)}, is_reusable=reusable
    )


def test_advise_generates_a_recommendation_for_an_open_finding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    monkeypatch.chdir(tmp_path)
    seed_repository_with_findings(tmp_path, findings=[OPEN_FINDING])
    mock_advice(httpx_mock, [{"title": "Ajouter des tests", "recommendation": "Créer tests/."}])

    result = runner.invoke(cli.app, ["advise", "Architecturor"])
    output = plain(result.output)

    assert result.exit_code == 0
    assert "Ajouter des tests" in output
    assert "development.tests" in output
    assert "1" in output and "conseillé" in output


def test_advise_skips_a_repository_without_open_findings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    monkeypatch.chdir(tmp_path)
    seed_repository_with_findings(tmp_path, findings=[OK_FINDING])

    result = runner.invoke(cli.app, ["advise", "Architecturor"])

    assert result.exit_code == 0
    assert "rien à recommander" in plain(result.output)
    assert httpx_mock.get_requests() == []


def test_advise_reports_an_unreachable_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    monkeypatch.chdir(tmp_path)
    seed_repository_with_findings(tmp_path, findings=[OPEN_FINDING])
    httpx_mock.add_exception(httpx.ConnectError("injoignable"), url=OLLAMA_GENERATE_URL)

    result = runner.invoke(cli.app, ["advise", "Architecturor"])
    output = plain(result.output)

    assert result.exit_code == 1
    assert "1 dépôt(s) en échec" in output
    assert "ollama serve" in output


def test_advise_no_save_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    monkeypatch.chdir(tmp_path)
    seed_repository_with_findings(tmp_path, findings=[OPEN_FINDING])
    mock_advice(httpx_mock, [{"title": "T", "recommendation": "R"}])

    result = runner.invoke(cli.app, ["advise", "Architecturor", "--no-save"])

    with sqlite3.connect(tmp_path / "data" / "githor.db") as connection:
        runs = connection.execute("SELECT COUNT(*) FROM advice_runs").fetchone()[0]

    assert result.exit_code == 0
    assert runs == 0
    assert "rien n'a été écrit en base" in plain(result.output)


def test_advise_adds_a_new_run_instead_of_replacing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    monkeypatch.chdir(tmp_path)
    seed_repository_with_findings(tmp_path, findings=[OPEN_FINDING])
    mock_advice(httpx_mock, [{"title": "T", "recommendation": "R"}], reusable=True)

    runner.invoke(cli.app, ["advise", "Architecturor"])
    runner.invoke(cli.app, ["advise", "Architecturor"])

    with sqlite3.connect(tmp_path / "data" / "githor.db") as connection:
        runs = connection.execute("SELECT COUNT(*) FROM advice_runs").fetchone()[0]

    assert runs == 2


def test_advise_rejects_an_unknown_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    seed_repository_with_findings(tmp_path, findings=[OPEN_FINDING])

    result = runner.invoke(cli.app, ["advise", "inconnu"])

    assert result.exit_code == 1
    assert "Repository inconnu" in plain(result.output)


def test_advise_without_a_database_says_what_to_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli.app, ["advise"])

    assert result.exit_code == 1
    assert "githor scan" in plain(result.output)


def test_advise_never_calls_github(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    monkeypatch.chdir(tmp_path)
    seed_repository_with_findings(tmp_path, findings=[OPEN_FINDING])
    mock_advice(httpx_mock, [{"title": "T", "recommendation": "R"}])

    runner.invoke(cli.app, ["advise", "Architecturor"])

    hosts = {request.url.host for request in httpx_mock.get_requests()}
    assert hosts == {"localhost"}


def test_advise_degrades_gracefully_on_invalid_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    monkeypatch.chdir(tmp_path)
    seed_repository_with_findings(tmp_path, findings=[OPEN_FINDING])
    httpx_mock.add_response(url=OLLAMA_GENERATE_URL, json={"response": "texte libre, pas du json"})

    result = runner.invoke(cli.app, ["advise", "Architecturor"])
    output = plain(result.output)

    assert result.exit_code == 0
    assert "non structurée" in output
    assert "texte libre, pas du json" in output


def test_advise_model_option_overrides_the_configured_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    monkeypatch.chdir(tmp_path)
    seed_repository_with_findings(tmp_path, findings=[OPEN_FINDING])
    mock_advice(httpx_mock, [{"title": "T", "recommendation": "R"}])

    runner.invoke(cli.app, ["advise", "Architecturor", "--model", "mistral"])

    request = httpx_mock.get_requests()[0]
    assert json.loads(request.content)["model"] == "mistral"


# ── githor ask (étape 31, §35) ───────────────────────────────────────────────


def mock_answer(httpx_mock: HTTPXMock, text: str, *, reusable: bool = False) -> None:
    """Simule une réponse en prose libre d'Ollama à /api/generate."""
    httpx_mock.add_response(url=OLLAMA_GENERATE_URL, json={"response": text}, is_reusable=reusable)


def test_ask_answers_the_question(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    monkeypatch.chdir(tmp_path)
    seed_repository_with_findings(tmp_path, findings=[OPEN_FINDING])
    mock_answer(httpx_mock, "Architecturor n'a pas de tests.")

    result = runner.invoke(cli.app, ["ask", "Quels projets n'ont pas de tests ?"])
    output = plain(result.output)

    assert result.exit_code == 0
    assert "Architecturor n'a pas de tests." in output
    assert "généré" in output.lower()
    assert "1 dépôt(s)" in output


def test_ask_requests_plain_text_not_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    monkeypatch.chdir(tmp_path)
    seed_repository_with_findings(tmp_path, findings=[OPEN_FINDING])
    mock_answer(httpx_mock, "Réponse.")

    runner.invoke(cli.app, ["ask", "Une question ?"])

    request = httpx_mock.get_requests()[0]
    assert "format" not in json.loads(request.content)


def test_ask_reports_an_unreachable_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    monkeypatch.chdir(tmp_path)
    seed_repository_with_findings(tmp_path, findings=[OPEN_FINDING])
    httpx_mock.add_exception(httpx.ConnectError("injoignable"), url=OLLAMA_GENERATE_URL)

    result = runner.invoke(cli.app, ["ask", "Une question ?"])
    output = plain(result.output)

    assert result.exit_code == 1
    assert "ollama serve" in output


def test_ask_without_a_database_says_what_to_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli.app, ["ask", "Une question ?"])

    assert result.exit_code == 1
    assert "githor scan" in plain(result.output)


def test_ask_with_an_empty_database_says_what_to_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    runner.invoke(cli.app, ["db", "init"])

    result = runner.invoke(cli.app, ["ask", "Une question ?"])

    assert result.exit_code == 1
    assert "githor scan" in plain(result.output)


def test_ask_never_calls_github(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    monkeypatch.chdir(tmp_path)
    seed_repository_with_findings(tmp_path, findings=[OPEN_FINDING])
    mock_answer(httpx_mock, "Réponse.")

    runner.invoke(cli.app, ["ask", "Une question ?"])

    hosts = {request.url.host for request in httpx_mock.get_requests()}
    assert hosts == {"localhost"}


def test_ask_model_option_overrides_the_configured_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    monkeypatch.chdir(tmp_path)
    seed_repository_with_findings(tmp_path, findings=[OPEN_FINDING])
    mock_answer(httpx_mock, "Réponse.")

    runner.invoke(cli.app, ["ask", "Une question ?", "--model", "mistral"])

    request = httpx_mock.get_requests()[0]
    assert json.loads(request.content)["model"] == "mistral"


def test_ask_summarises_every_registered_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    """La question porte sur tout le parc, pas seulement un dépôt nommé."""
    monkeypatch.chdir(tmp_path)
    seed_repository_with_findings(tmp_path, findings=[OPEN_FINDING])
    mock_answer(httpx_mock, "Réponse.")

    runner.invoke(cli.app, ["ask", "Une question ?"])

    prompt = json.loads(httpx_mock.get_requests()[0].content)["prompt"]
    assert "nouhailler/Architecturor" in prompt


# ── githor tui (étape 35) ────────────────────────────────────────────────────


def test_tui_is_listed_in_help() -> None:
    result = runner.invoke(cli.app, ["--help"])

    assert "tui" in plain(result.output)


def test_tui_without_a_database_says_what_to_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli.app, ["tui"])

    assert result.exit_code == 1
    assert "githor scan" in plain(result.output)


# ── githor web (étape 39) ────────────────────────────────────────────────────


def test_web_is_listed_in_help() -> None:
    result = runner.invoke(cli.app, ["--help"])

    assert "web" in plain(result.output)


def test_web_without_a_database_says_what_to_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli.app, ["web"])

    assert result.exit_code == 1
    assert "githor scan" in plain(result.output)
