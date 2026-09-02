"""Tests de la résolution du jeton GitHub (étape 5).

La CLI ``gh`` réelle n'est jamais appelée : ``subprocess.run`` est remplacé.
"""

import subprocess
from typing import Any

import pytest

from githor.config import GITHUB_TOKEN_ENV
from githor.github import token as token_module
from githor.github.errors import AuthenticationError
from githor.github.token import (
    GH_COMMAND,
    ResolvedToken,
    TokenSource,
    find_token,
    require_token,
    token_from_gh_cli,
)

GH_TOKEN = "gho_jeton_renvoye_par_gh"
ENV_TOKEN = "ghp_jeton_de_l_environnement"


def fake_gh(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: str = "",
    returncode: int = 0,
    raises: BaseException | None = None,
) -> list[tuple[Any, ...]]:
    """Remplace ``subprocess.run`` et enregistre les appels effectués."""
    calls: list[tuple[Any, ...]] = []

    def run(command: tuple[str, ...], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr="")

    monkeypatch.setattr(token_module.subprocess, "run", run)
    return calls


# ── Interrogation de gh ──────────────────────────────────────────────────────


def test_gh_token_is_read_and_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_gh(monkeypatch, stdout=f"{GH_TOKEN}\n")

    assert token_from_gh_cli() == GH_TOKEN
    assert calls == [GH_COMMAND]


def test_gh_absent_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_gh(monkeypatch, raises=FileNotFoundError())

    assert token_from_gh_cli() is None


def test_gh_not_logged_in_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_gh(monkeypatch, returncode=1)

    assert token_from_gh_cli() is None


def test_gh_empty_output_is_treated_as_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_gh(monkeypatch, stdout="  \n")

    assert token_from_gh_cli() is None


def test_gh_timeout_is_reported_and_survivable(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    fake_gh(monkeypatch, raises=subprocess.TimeoutExpired(GH_COMMAND, 10.0))

    with caplog.at_level("WARNING", logger="githor.github.token"):
        assert token_from_gh_cli() is None

    assert any("gh auth token" in record.message for record in caplog.records)


def test_gh_token_is_never_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    fake_gh(monkeypatch, stdout=GH_TOKEN)

    with caplog.at_level("DEBUG", logger="githor.github.token"):
        find_token()

    assert GH_TOKEN not in caplog.text


# ── Priorité des sources ─────────────────────────────────────────────────────


def test_environment_wins_over_gh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GITHUB_TOKEN_ENV, ENV_TOKEN)
    calls = fake_gh(monkeypatch, stdout=GH_TOKEN)

    token = find_token()

    assert token is not None
    assert token.value == ENV_TOKEN
    assert token.source is TokenSource.ENVIRONMENT
    assert calls == []  # gh n'est même pas sollicité


def test_gh_is_used_when_environment_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_gh(monkeypatch, stdout=GH_TOKEN)

    token = find_token()

    assert token is not None
    assert token.value == GH_TOKEN
    assert token.source is TokenSource.GH_CLI


def test_gh_is_not_called_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_gh(monkeypatch, stdout=GH_TOKEN)

    assert find_token(allow_gh_cli=False) is None
    assert calls == []


def test_find_token_returns_none_without_any_source() -> None:
    assert find_token() is None


# ── require_token ────────────────────────────────────────────────────────────


def test_require_token_suggests_gh_login_when_allowed() -> None:
    with pytest.raises(AuthenticationError) as caught:
        require_token()

    assert "gh auth login" in str(caught.value)
    assert GITHUB_TOKEN_ENV in str(caught.value)


def test_require_token_omits_gh_when_disabled() -> None:
    with pytest.raises(AuthenticationError) as caught:
        require_token(allow_gh_cli=False)

    assert "gh auth login" not in str(caught.value)
    assert GITHUB_TOKEN_ENV in str(caught.value)


def test_require_token_returns_the_environment_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GITHUB_TOKEN_ENV, ENV_TOKEN)

    assert require_token().value == ENV_TOKEN


# ── Confidentialité ──────────────────────────────────────────────────────────


def test_token_value_is_absent_from_repr() -> None:
    token = ResolvedToken(value="ghp_ne_doit_pas_fuiter", source=TokenSource.ENVIRONMENT)

    assert "ghp_ne_doit_pas_fuiter" not in repr(token)


@pytest.mark.parametrize(
    ("source", "expected"),
    [(TokenSource.ENVIRONMENT, GITHUB_TOKEN_ENV), (TokenSource.GH_CLI, "gh CLI")],
)
def test_description_names_the_source_only(source: TokenSource, expected: str) -> None:
    token = ResolvedToken(value="ghp_secret", source=source)

    assert expected in token.description
    assert "ghp_secret" not in token.description
