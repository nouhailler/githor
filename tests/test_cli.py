"""Tests de la CLI minimale (étape 2) : aide, version, options globales, erreurs."""

import logging
import re

import pytest
import typer
from typer.testing import CliRunner

from githor import __version__, cli

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
    monkeypatch.setitem(cli._state, "debug", False)

    assert cli.main() == 1
    assert "boum" in plain(capsys.readouterr().err)


def test_main_reraises_in_debug_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise RuntimeError("boum")

    monkeypatch.setattr(cli, "app", boom)
    monkeypatch.setitem(cli._state, "debug", True)

    with pytest.raises(RuntimeError):
        cli.main()


def test_main_handles_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupted() -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "app", interrupted)
    assert cli.main() == 130
