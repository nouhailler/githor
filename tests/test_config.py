"""Tests de la configuration (étape 3) : découverte, validation, chemins, token."""

from pathlib import Path

import pytest

from githor.config import (
    CONFIG_PATH_ENV,
    GITHUB_TOKEN_ENV,
    Config,
    find_config_file,
    get_github_token,
    load_config,
)
from githor.errors import ConfigError, GithorError

FULL_CONFIG = """
[github]
api_url = "https://github.example.com/api/v3"

[scan]
include_forks = true
include_archived = true
commit_history_days = 30
snapshot_freshness_hours = 12

[storage]
database = "db/custom.db"

[export]
directory = "out"
"""


def write(path: Path, content: str) -> Path:
    """Écrit un fichier de configuration temporaire."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ── Valeurs par défaut ───────────────────────────────────────────────────────


def test_defaults_apply_without_any_file(tmp_path: Path) -> None:
    config = load_config(base_dir=tmp_path)

    assert config.source is None
    assert config.github.api_url == "https://api.github.com"
    assert config.scan.include_forks is False
    assert config.scan.include_archived is False
    assert config.scan.commit_history_days == 90
    assert config.scan.snapshot_freshness_hours == 0
    assert config.storage.database == tmp_path.resolve() / "data" / "githor.db"
    assert config.export.directory == tmp_path.resolve() / "data" / "exports"


# ── Découverte du fichier ────────────────────────────────────────────────────


def test_project_config_is_discovered(tmp_path: Path) -> None:
    expected = write(tmp_path / "config" / "config.toml", FULL_CONFIG)

    config = load_config(base_dir=tmp_path)

    assert config.source == expected
    assert config.scan.commit_history_days == 30


def test_environment_variable_wins_over_project_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(tmp_path / "config" / "config.toml", "[scan]\ncommit_history_days = 30\n")
    from_env = write(tmp_path / "ailleurs.toml", "[scan]\ncommit_history_days = 45\n")
    monkeypatch.setenv(CONFIG_PATH_ENV, str(from_env))

    config = load_config(base_dir=tmp_path)

    assert config.source == from_env
    assert config.scan.commit_history_days == 45


def test_explicit_path_wins_over_environment_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(CONFIG_PATH_ENV, str(write(tmp_path / "env.toml", "")))
    explicit = write(tmp_path / "explicite.toml", "[scan]\ncommit_history_days = 7\n")

    config = load_config(explicit, base_dir=tmp_path)

    assert config.source == explicit
    assert config.scan.commit_history_days == 7


def test_find_config_file_returns_none_when_nothing_exists(tmp_path: Path) -> None:
    assert find_config_file(tmp_path) is None


# ── Erreurs ──────────────────────────────────────────────────────────────────


def test_missing_explicit_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="introuvable"):
        load_config(tmp_path / "absent.toml", base_dir=tmp_path)


def test_invalid_toml_is_reported(tmp_path: Path) -> None:
    path = write(tmp_path / "bad.toml", "[scan\ncassé")

    with pytest.raises(ConfigError, match="TOML invalide"):
        load_config(path, base_dir=tmp_path)


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path / "typo.toml", "[scan]\ninclude_fork = true\n")

    with pytest.raises(ConfigError, match="scan.include_fork"):
        load_config(path, base_dir=tmp_path)


def test_unknown_section_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path / "section.toml", "[ollama]\nmodel = 'llama3'\n")

    with pytest.raises(ConfigError, match="ollama"):
        load_config(path, base_dir=tmp_path)


@pytest.mark.parametrize("days", [0, -1, 4000])
def test_commit_history_days_is_bounded(tmp_path: Path, days: int) -> None:
    path = write(tmp_path / "range.toml", f"[scan]\ncommit_history_days = {days}\n")

    with pytest.raises(ConfigError, match="commit_history_days"):
        load_config(path, base_dir=tmp_path)


def test_snapshot_freshness_is_read(tmp_path: Path) -> None:
    path = write(tmp_path / "frais.toml", "[scan]\nsnapshot_freshness_hours = 12\n")

    assert load_config(path, base_dir=tmp_path).scan.snapshot_freshness_hours == 12


@pytest.mark.parametrize("hours", [-1, 10_000])
def test_snapshot_freshness_is_bounded(tmp_path: Path, hours: int) -> None:
    """Zéro est légitime — il désactive la dispense ; une valeur négative ne l'est pas."""
    path = write(tmp_path / "frais.toml", f"[scan]\nsnapshot_freshness_hours = {hours}\n")

    with pytest.raises(ConfigError, match="snapshot_freshness_hours"):
        load_config(path, base_dir=tmp_path)


def test_config_errors_are_githor_errors(tmp_path: Path) -> None:
    with pytest.raises(GithorError):
        load_config(tmp_path / "absent.toml", base_dir=tmp_path)


# ── api_url ──────────────────────────────────────────────────────────────────


def test_api_url_trailing_slash_is_removed(tmp_path: Path) -> None:
    path = write(tmp_path / "url.toml", '[github]\napi_url = "https://api.github.com/"\n')

    assert load_config(path, base_dir=tmp_path).github.api_url == "https://api.github.com"


def test_api_url_requires_http_scheme(tmp_path: Path) -> None:
    path = write(tmp_path / "url.toml", '[github]\napi_url = "ftp://exemple"\n')

    with pytest.raises(ConfigError, match="http"):
        load_config(path, base_dir=tmp_path)


# ── Résolution des chemins ───────────────────────────────────────────────────


def test_relative_paths_are_resolved_against_base_dir(tmp_path: Path) -> None:
    path = write(tmp_path / "paths.toml", FULL_CONFIG)

    config = load_config(path, base_dir=tmp_path)

    assert config.storage.database == tmp_path.resolve() / "db" / "custom.db"
    assert config.export.directory == tmp_path.resolve() / "out"


def test_absolute_paths_are_preserved(tmp_path: Path) -> None:
    absolute = tmp_path / "ailleurs" / "githor.db"
    path = write(tmp_path / "abs.toml", f'[storage]\ndatabase = "{absolute}"\n')

    assert load_config(path, base_dir=tmp_path).storage.database == absolute


# ── Token ────────────────────────────────────────────────────────────────────


def test_token_is_none_when_unset() -> None:
    assert get_github_token() is None


def test_blank_token_is_treated_as_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GITHUB_TOKEN_ENV, "   ")
    assert get_github_token() is None


def test_token_is_read_and_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(GITHUB_TOKEN_ENV, "  ghp_exemple  ")
    assert get_github_token() == "ghp_exemple"


def test_config_never_carries_the_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(GITHUB_TOKEN_ENV, "ghp_ne_doit_pas_fuiter")

    config = load_config(base_dir=tmp_path)

    assert "ghp_ne_doit_pas_fuiter" not in repr(config)
    assert "ghp_ne_doit_pas_fuiter" not in str(config.model_dump())


def test_example_file_matches_the_model() -> None:
    """Le fichier livré doit rester valide au regard du modèle."""
    example = Path(__file__).resolve().parents[1] / "config" / "config.toml.example"

    config = load_config(example)

    assert isinstance(config, Config)
    assert config.scan.commit_history_days == 90
    assert config.scan.snapshot_freshness_hours == 0


def test_gh_cli_fallback_is_enabled_by_default(tmp_path: Path) -> None:
    assert load_config(base_dir=tmp_path).github.use_gh_cli is True


def test_gh_cli_fallback_can_be_disabled(tmp_path: Path) -> None:
    path = write(tmp_path / "gh.toml", "[github]\nuse_gh_cli = false\n")

    assert load_config(path, base_dir=tmp_path).github.use_gh_cli is False
