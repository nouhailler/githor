"""Clone superficiel et mise à jour du miroir local d'un dépôt.

Githor analyse le code en le lisant sur disque : il lui faut donc une copie
locale. Trois règles gouvernent ce module.

**Le miroir est en lecture seule vis-à-vis de GitHub.** Aucune commande
sortante n'écrit quoi que ce soit : ni ``push``, ni ``tag``, ni création de
branche. La copie sert à lire, jamais à publier.

**Le jeton ne passe pas par l'URL.** Coudre un jeton dans l'adresse de clone
l'écrirait en clair dans ``.git/config`` du miroir, où il survivrait à
l'exécution — exactement ce que Githor s'interdit. L'authentification des
dépôts privés est donc laissée à ``git`` et à son gestionnaire d'identifiants
habituel (``gh auth setup-git``). ``GIT_TERMINAL_PROMPT=0`` garantit qu'un
dépôt inaccessible échoue tout de suite au lieu d'attendre une saisie qui ne
viendra jamais.

**Un répertoire qui n'est pas notre miroir n'est jamais touché.** Avant toute
mise à jour, l'``origin`` du dépôt local est comparé à l'URL attendue ; en cas
de désaccord, Githor refuse d'agir. ``git reset --hard`` détruit du travail :
il ne doit s'exécuter que sur une copie dont Githor est l'auteur.
"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from githor.errors import GithorError
from githor.logging import get_logger

logger = get_logger("vcs.git")

GIT_ENVIRONMENT = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "",
    "SSH_ASKPASS": "",
    "GIT_CONFIG_NOSYSTEM": "1",
    "LC_ALL": "C",
}
"""Environnement imposé à chaque appel de ``git``.

Les variables d'invite sont neutralisées pour qu'un dépôt privé sans
identifiants échoue immédiatement : un scan de 77 dépôts ne peut pas se
permettre de rester bloqué sur une demande de mot de passe.
"""

_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")
"""Ce qu'un segment de chemin a le droit de contenir.

Le motif interdit déjà les séparateurs ; ``.`` et ``..`` le satisfont pourtant
et sont écartés à part, faute de quoi un nom bien formé remonterait hors de
l'espace de travail.
"""

_RESERVED_SEGMENTS = frozenset({".", ".."})


class GitError(GithorError):
    """Commande ``git`` en échec, absente ou appliquée à un miroir inattendu."""


class GitNotFoundError(GitError):
    """L'exécutable ``git`` est introuvable sur cette machine."""


@dataclass(frozen=True)
class Checkout:
    """Copie locale d'un dépôt, prête à être lue."""

    full_name: str
    path: Path
    """Racine de la copie de travail."""

    head: str
    """SHA du commit analysé — l'analyse porte sur un état daté, nommable."""

    branch: str
    created: bool
    """Vrai si le miroir vient d'être créé par cet appel."""

    fetched: bool
    """Vrai si le miroir a été rafraîchi depuis l'origine par cet appel."""

    @property
    def short_head(self) -> str:
        """SHA abrégé, tel que ``git`` l'affiche."""
        return self.head[:7]


def clone_url_for(url: str) -> str:
    """Déduit l'URL de clone d'une adresse de dépôt.

    GitHub sert le même dépôt sur ``https://github.com/o/n`` et sur
    ``https://github.com/o/n.git`` ; c'est la seconde forme qui est l'adresse de
    clone. Toute autre adresse — un chemin local, une URL déjà suffixée — est
    reprise telle quelle : deviner davantage reviendrait à réécrire ce que
    l'utilisateur a fourni.

    Args:
        url: adresse du dépôt, telle que Githor l'a enregistrée.
    """
    trimmed = url.strip().rstrip("/")
    if trimmed.startswith(("http://", "https://")) and not trimmed.endswith(".git"):
        return f"{trimmed}.git"
    return trimmed


def checkout_path(workspace: Path, full_name: str) -> Path:
    """Emplacement du miroir d'un dépôt sous l'espace de travail.

    Le nom complet ``propriétaire/dépôt`` devient deux niveaux de répertoires,
    ce qui évite toute collision entre deux dépôts homonymes appartenant à des
    propriétaires différents.

    Args:
        workspace: racine de l'espace de travail.
        full_name: nom complet du dépôt, de la forme ``propriétaire/dépôt``.

    Raises:
        GitError: si le nom complet ne peut pas devenir un chemin sûr.
    """
    segments = full_name.strip().split("/")
    if len(segments) != 2 or not all(_is_safe_segment(segment) for segment in segments):
        raise GitError(
            f"Nom de dépôt inexploitable comme chemin : {full_name!r}. "
            "Un nom complet s'écrit « propriétaire/dépôt »."
        )
    return workspace / segments[0] / segments[1]


def _is_safe_segment(segment: str) -> bool:
    """Dit si un segment peut devenir un nom de répertoire sans risque."""
    return segment not in _RESERVED_SEGMENTS and _SEGMENT.match(segment) is not None


def _run(
    arguments: tuple[str, ...], *, cwd: Path | None = None, timeout: float
) -> subprocess.CompletedProcess[str]:
    """Exécute une commande ``git`` et traduit ses échecs en :class:`GitError`.

    Args:
        arguments: arguments passés à ``git``, sans l'exécutable lui-même.
        cwd: répertoire d'exécution ; celui du processus par défaut.
        timeout: délai maximal, en secondes.

    Raises:
        GitNotFoundError: si ``git`` n'est pas installé.
        GitError: si la commande échoue, dépasse le délai ou ne peut être lancée.
    """
    command = ("git", *arguments)
    try:
        result = subprocess.run(  # noqa: S603 — arguments construits, jamais de shell
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=cwd,
            env=GIT_ENVIRONMENT,
        )
    except FileNotFoundError as exc:
        raise GitNotFoundError(
            "L'exécutable « git » est introuvable. Installez-le "
            "(« sudo apt install git ») pour analyser le code localement."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(
            f"« git {arguments[0]} » n'a pas répondu en {timeout:.0f} s. "
            "Le dépôt est peut-être volumineux : relevez audit.git_timeout_seconds."
        ) from exc
    except OSError as exc:
        raise GitError(f"« git {arguments[0]} » n'a pas pu être exécuté : {exc}") from exc

    if result.returncode != 0:
        raise GitError(f"« git {arguments[0]} » a échoué : {_first_error_line(result.stderr)}")

    return result


def _first_error_line(stderr: str) -> str:
    """Retient la première ligne utile de la sortie d'erreur de ``git``.

    ``git`` est bavard ; l'utilisateur n'a besoin que du motif de l'échec.
    """
    for line in stderr.splitlines():
        cleaned = line.strip()
        if cleaned and not cleaned.lower().startswith(("cloning into", "warning:")):
            return cleaned
    return "aucun détail fourni"


def git_version(*, timeout: float = 10.0) -> str:
    """Retourne la version de ``git`` installée.

    Sert de vérification préalable : mieux vaut échouer avant d'avoir cloné
    soixante-dix dépôts que pendant.

    Raises:
        GitNotFoundError: si ``git`` n'est pas installé.
    """
    return _run(("--version",), timeout=timeout).stdout.strip()


def head_commit(path: Path, *, timeout: float = 30.0) -> str:
    """SHA du commit sur lequel une copie locale est positionnée."""
    return _run(("rev-parse", "HEAD"), cwd=path, timeout=timeout).stdout.strip()


def _origin_url(path: Path, *, timeout: float) -> str | None:
    """URL du dépôt d'origine d'une copie locale, ``None`` si elle n'en a pas."""
    try:
        return _run(("remote", "get-url", "origin"), cwd=path, timeout=timeout).stdout.strip()
    except GitError:
        return None


def _depth_arguments(depth: int) -> tuple[str, ...]:
    """Traduit la profondeur configurée en arguments ``git``.

    ``0`` signifie « tout l'historique » : c'est l'absence d'option, non
    ``--depth 0``, que ``git`` refuserait.
    """
    return () if depth <= 0 else ("--depth", str(depth))


def ensure_checkout(
    *,
    url: str,
    full_name: str,
    branch: str,
    workspace: Path,
    depth: int = 1,
    timeout: float = 300.0,
    fetch: bool = True,
) -> Checkout:
    """Garantit qu'une copie locale à jour du dépôt existe, et la décrit.

    Trois situations, une seule fonction :

    - **rien sur disque** : le dépôt est cloné, superficiellement et sur la
      seule branche demandée ;
    - **un miroir déjà présent** : il est rafraîchi, sauf si ``fetch`` est faux ;
    - **un miroir présent mais hors ligne** (``fetch=False``) : il est lu tel
      quel, ce qui permet d'analyser sans réseau.

    Args:
        url: adresse de clone, telle que rendue par :func:`clone_url_for`.
        full_name: nom complet du dépôt, qui détermine le chemin du miroir.
        branch: branche à suivre, typiquement la branche par défaut du dépôt.
        workspace: racine de l'espace de travail.
        depth: profondeur du clone superficiel ; ``0`` pour tout l'historique.
        timeout: délai maximal accordé à chaque commande ``git``.
        fetch: autorise le contact avec l'origine.

    Raises:
        GitError: si le clone échoue, si le miroir attendu est absent alors que
            ``fetch`` est faux, ou si le répertoire visé appartient à un autre
            dépôt.
    """
    target = checkout_path(workspace, full_name)
    existing = (target / ".git").exists()

    if not existing:
        if not fetch:
            raise GitError(
                f"Aucune copie locale de {full_name} dans {target}. "
                "Retirez --offline pour la cloner."
            )
        _clone(url=url, branch=branch, target=target, depth=depth, timeout=timeout)
        head = head_commit(target, timeout=timeout)
        logger.debug("Miroir créé : %s -> %s (%s)", full_name, target, head[:7])
        return Checkout(
            full_name=full_name,
            path=target,
            head=head,
            branch=branch,
            created=True,
            fetched=True,
        )

    _refuse_foreign_checkout(target, url=url, full_name=full_name, timeout=timeout)

    if fetch:
        _update(target, branch=branch, depth=depth, timeout=timeout)

    head = head_commit(target, timeout=timeout)
    return Checkout(
        full_name=full_name,
        path=target,
        head=head,
        branch=branch,
        created=False,
        fetched=fetch,
    )


def _clone(*, url: str, branch: str, target: Path, depth: int, timeout: float) -> None:
    """Clone le dépôt, en refusant d'écrire dans un répertoire déjà occupé."""
    if target.exists() and any(target.iterdir()):
        raise GitError(
            f"{target} existe déjà et n'est pas un dépôt git. "
            "Videz ce répertoire ou choisissez un autre audit.workspace."
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    _run(
        (
            "clone",
            "--quiet",
            "--single-branch",
            "--branch",
            branch,
            *_depth_arguments(depth),
            url,
            str(target),
        ),
        timeout=timeout,
    )


def _refuse_foreign_checkout(target: Path, *, url: str, full_name: str, timeout: float) -> None:
    """Vérifie que la copie locale est bien celle du dépôt attendu.

    Sans ce garde-fou, un ``audit.workspace`` mal choisi ferait tomber un
    ``git reset --hard`` sur le travail en cours de l'utilisateur.
    """
    origin = _origin_url(target, timeout=timeout)
    if origin is None:
        raise GitError(
            f"{target} est un dépôt git sans origine : Githor n'y touchera pas. "
            "Ce répertoire n'a pas été créé par lui."
        )
    if _comparable(origin) != _comparable(url):
        raise GitError(
            f"{target} suit {origin}, pas {url} : Githor refuse de le mettre à jour. "
            f"Déplacez ce répertoire pour laisser Githor cloner {full_name} lui-même."
        )


def _comparable(url: str) -> str:
    """Normalise une URL pour comparer deux adresses du même dépôt."""
    return clone_url_for(url).lower()


def _update(target: Path, *, branch: str, depth: int, timeout: float) -> None:
    """Rafraîchit un miroir existant à l'état de la branche distante.

    Le miroir est une copie jetable : ``reset --hard`` puis ``clean`` le
    ramènent exactement à l'état publié. C'est ce qui garantit qu'une analyse
    décrit le dépôt distant, et non les résidus d'une analyse précédente.
    """
    _run(
        ("fetch", "--quiet", *_depth_arguments(depth), "origin", branch),
        cwd=target,
        timeout=timeout,
    )
    _run(("reset", "--quiet", "--hard", "FETCH_HEAD"), cwd=target, timeout=timeout)
    _run(("clean", "--quiet", "-fd"), cwd=target, timeout=timeout)
