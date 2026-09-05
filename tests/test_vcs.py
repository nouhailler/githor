"""Tests du miroir local : clone superficiel, mise à jour, garde-fous.

Aucun test ne joint le réseau : les dépôts « distants » sont de vrais dépôts
git créés dans un répertoire temporaire, et clonés par chemin de fichier. Ce
qui est vérifié est donc le comportement réel de ``git``, pas une imitation.
"""

import subprocess
from pathlib import Path
from typing import Any

import pytest

from githor.vcs.git import (
    Checkout,
    GitError,
    checkout_path,
    clone_url_for,
    ensure_checkout,
    git_version,
    head_commit,
)


def run_git(*arguments: str, cwd: Path) -> str:
    """Lance une commande git dans un dépôt de test."""
    result = subprocess.run(
        ("git", *arguments),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    """Crée un dépôt git servant d'origine, avec un commit sur « main »."""
    origin = tmp_path / "origine"
    origin.mkdir()
    run_git("init", "--quiet", "--initial-branch", "main", cwd=origin)
    run_git("config", "user.email", "test@githor.local", cwd=origin)
    run_git("config", "user.name", "Test Githor", cwd=origin)
    (origin / "module.py").write_text("VALEUR = 1\n", encoding="utf-8")
    run_git("add", ".", cwd=origin)
    run_git("commit", "--quiet", "-m", "Premier commit", cwd=origin)
    return origin


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Racine de l'espace de travail des miroirs."""
    return tmp_path / "miroirs"


def mirror(remote: Path, workspace: Path, **options: Any) -> Checkout:
    """Clone le dépôt de test sous « proprio/depot ».

    L'origine est donnée sous forme d'URL ``file://`` et non de simple chemin :
    git optimise les clones locaux par liens durs et y ignore ``--depth``, ce
    qui ne dirait rien du comportement réel face à un dépôt distant.
    """
    return ensure_checkout(
        url=f"file://{remote}",
        full_name="proprio/depot",
        branch="main",
        workspace=workspace,
        **options,
    )


# ── URL de clone ─────────────────────────────────────────────────────────────


def test_a_github_url_gains_the_git_suffix() -> None:
    assert clone_url_for("https://github.com/nouhailler/Githor") == (
        "https://github.com/nouhailler/Githor.git"
    )


def test_a_trailing_slash_is_not_kept() -> None:
    assert clone_url_for("https://github.com/nouhailler/Githor/") == (
        "https://github.com/nouhailler/Githor.git"
    )


def test_an_already_suffixed_url_is_left_alone() -> None:
    url = "https://github.com/nouhailler/Githor.git"
    assert clone_url_for(url) == url


def test_a_local_path_is_left_alone() -> None:
    assert clone_url_for("/tmp/depot") == "/tmp/depot"


# ── Chemin du miroir ─────────────────────────────────────────────────────────


def test_the_full_name_becomes_two_directories(tmp_path: Path) -> None:
    assert checkout_path(tmp_path, "nouhailler/Githor") == tmp_path / "nouhailler" / "Githor"


@pytest.mark.parametrize(
    "name",
    ["Githor", "a/b/c", "../../etc", "proprio/..", "proprio/dé pôt", ""],
)
def test_a_name_that_is_not_a_safe_path_is_refused(tmp_path: Path, name: str) -> None:
    """Un nom qui s'échapperait de l'espace de travail ne doit jamais devenir un chemin."""
    with pytest.raises(GitError, match="inexploitable"):
        checkout_path(tmp_path, name)


# ── Clone ────────────────────────────────────────────────────────────────────


def test_a_missing_mirror_is_cloned(remote: Path, workspace: Path) -> None:
    checkout = mirror(remote, workspace)

    assert checkout.created is True
    assert (workspace / "proprio" / "depot" / "module.py").read_text() == "VALEUR = 1\n"


def test_the_checkout_names_the_analysed_commit(remote: Path, workspace: Path) -> None:
    """Une analyse doit pouvoir dire sur quel état exact elle porte."""
    checkout = mirror(remote, workspace)

    assert checkout.head == run_git("rev-parse", "HEAD", cwd=remote)
    assert checkout.short_head == checkout.head[:7]


def test_a_shallow_clone_keeps_one_commit(remote: Path, workspace: Path) -> None:
    (remote / "autre.py").write_text("AUTRE = 2\n", encoding="utf-8")
    run_git("add", ".", cwd=remote)
    run_git("commit", "--quiet", "-m", "Deuxième commit", cwd=remote)

    mirror(remote, workspace, depth=1)

    history = run_git("rev-list", "--count", "HEAD", cwd=workspace / "proprio" / "depot")
    assert history == "1"


def test_a_depth_of_zero_keeps_the_whole_history(remote: Path, workspace: Path) -> None:
    run_git("commit", "--quiet", "--allow-empty", "-m", "Deuxième commit", cwd=remote)

    mirror(remote, workspace, depth=0)

    history = run_git("rev-list", "--count", "HEAD", cwd=workspace / "proprio" / "depot")
    assert history == "2"


def test_an_unreachable_repository_is_reported(workspace: Path, tmp_path: Path) -> None:
    with pytest.raises(GitError, match="clone"):
        ensure_checkout(
            url=str(tmp_path / "inexistant"),
            full_name="proprio/depot",
            branch="main",
            workspace=workspace,
        )


def test_a_non_empty_directory_is_never_clobbered(remote: Path, workspace: Path) -> None:
    """Le clone ne doit pas écrire par-dessus le travail de quelqu'un."""
    target = workspace / "proprio" / "depot"
    target.mkdir(parents=True)
    (target / "important.txt").write_text("à ne pas perdre", encoding="utf-8")

    with pytest.raises(GitError, match="existe déjà"):
        mirror(remote, workspace)

    assert (target / "important.txt").read_text() == "à ne pas perdre"


# ── Mise à jour ──────────────────────────────────────────────────────────────


def test_an_existing_mirror_is_updated(remote: Path, workspace: Path) -> None:
    mirror(remote, workspace)
    (remote / "module.py").write_text("VALEUR = 2\n", encoding="utf-8")
    run_git("commit", "--quiet", "-am", "Mise à jour", cwd=remote)

    checkout = mirror(remote, workspace)

    assert checkout.created is False
    assert (workspace / "proprio" / "depot" / "module.py").read_text() == "VALEUR = 2\n"


def test_a_removed_file_disappears_from_the_mirror(remote: Path, workspace: Path) -> None:
    """Le miroir décrit le dépôt publié, pas la somme des analyses passées."""
    mirror(remote, workspace)
    run_git("rm", "--quiet", "module.py", cwd=remote)
    run_git("commit", "--quiet", "-m", "Suppression", cwd=remote)

    mirror(remote, workspace)

    assert not (workspace / "proprio" / "depot" / "module.py").exists()


def test_local_leftovers_are_cleaned_away(remote: Path, workspace: Path) -> None:
    mirror(remote, workspace)
    (workspace / "proprio" / "depot" / "résidu.py").write_text("x = 1\n", encoding="utf-8")

    mirror(remote, workspace)

    assert not (workspace / "proprio" / "depot" / "résidu.py").exists()


def test_offline_reads_the_mirror_without_touching_the_origin(
    remote: Path, workspace: Path
) -> None:
    mirror(remote, workspace)
    (remote / "module.py").write_text("VALEUR = 3\n", encoding="utf-8")
    run_git("commit", "--quiet", "-am", "Non récupéré", cwd=remote)

    checkout = mirror(remote, workspace, fetch=False)

    assert checkout.fetched is False
    assert (workspace / "proprio" / "depot" / "module.py").read_text() == "VALEUR = 1\n"


def test_offline_without_a_mirror_is_an_actionable_error(remote: Path, workspace: Path) -> None:
    with pytest.raises(GitError, match="--offline"):
        mirror(remote, workspace, fetch=False)


# ── Garde-fous ───────────────────────────────────────────────────────────────


def test_a_checkout_of_another_repository_is_refused(
    remote: Path, workspace: Path, tmp_path: Path
) -> None:
    """Githor ne lance jamais « reset --hard » sur un dépôt qu'il n'a pas cloné."""
    autre = tmp_path / "autre"
    autre.mkdir()
    run_git("init", "--quiet", "--initial-branch", "main", cwd=autre)
    run_git("config", "user.email", "test@githor.local", cwd=autre)
    run_git("config", "user.name", "Test Githor", cwd=autre)
    (autre / "travail.py").write_text("EN_COURS = True\n", encoding="utf-8")
    run_git("add", ".", cwd=autre)
    run_git("commit", "--quiet", "-m", "Travail local", cwd=autre)

    target = workspace / "proprio" / "depot"
    target.parent.mkdir(parents=True)
    run_git("clone", "--quiet", str(autre), str(target), cwd=tmp_path)

    with pytest.raises(GitError, match="refuse"):
        mirror(remote, workspace)

    assert (target / "travail.py").read_text() == "EN_COURS = True\n"


def test_a_checkout_without_an_origin_is_refused(remote: Path, workspace: Path) -> None:
    target = workspace / "proprio" / "depot"
    target.mkdir(parents=True)
    run_git("init", "--quiet", cwd=target)

    with pytest.raises(GitError, match="sans origine"):
        mirror(remote, workspace)


def test_a_missing_git_is_named_as_such(monkeypatch: pytest.MonkeyPatch) -> None:
    def absent(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", absent)

    with pytest.raises(GitError, match="introuvable"):
        git_version()


def test_a_timeout_suggests_the_setting_to_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    def too_slow(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="git", timeout=1.0)

    monkeypatch.setattr(subprocess, "run", too_slow)

    with pytest.raises(GitError, match="git_timeout_seconds"):
        git_version()


# ── Version et HEAD ──────────────────────────────────────────────────────────


def test_git_version_is_reported() -> None:
    assert git_version().startswith("git version")


def test_head_commit_reads_the_current_sha(remote: Path) -> None:
    assert head_commit(remote) == run_git("rev-parse", "HEAD", cwd=remote)
