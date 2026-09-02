"""Collecte de l'activité récente d'un repository.

Seule la fenêtre configurée est téléchargée : mesurer l'activité récente ne
justifie pas de rapatrier l'historique complet d'un dépôt.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from githor.github.client import GitHubClient
from githor.github.errors import EmptyRepositoryError, NotFoundError
from githor.github.repositories import MAX_COMMIT_PAGES, list_commits
from githor.logging import get_logger
from githor.models.activity import Activity, Commit
from githor.utils.dates import parse_datetime, utc_now

logger = get_logger("collectors.activity")

PAGE_SIZE = 100
COMMIT_CEILING = MAX_COMMIT_PAGES * PAGE_SIZE
"""Nombre de commits au-delà duquel la fenêtre est considérée comme tronquée."""


def normalise_commit(payload: dict[str, Any]) -> Commit | None:
    """Traduit un commit GitHub en modèle normalisé.

    Returns:
        ``None`` si la charge utile n'est pas exploitable : un commit illisible
        ne doit pas interrompre la mesure d'activité de tout un dépôt.
    """
    sha = payload.get("sha")
    if not isinstance(sha, str) or not sha:
        return None

    details = payload.get("commit")
    details = details if isinstance(details, dict) else {}
    author = details.get("author")
    author = author if isinstance(author, dict) else {}

    try:
        committed_at = parse_datetime(author.get("date"))
    except ValueError:
        committed_at = None

    message = details.get("message")

    return Commit(
        sha=sha,
        author=author.get("name"),
        message=message if isinstance(message, str) else None,
        committed_at=committed_at,
    )


def summarise(
    commits: Iterable[Commit], *, window_days: int, now: datetime | None = None
) -> Activity:
    """Résume une liste de commits en indicateurs d'activité.

    Les décomptes sur 30 et 90 jours ne sont produits que si la fenêtre
    téléchargée les couvre : mieux vaut une valeur absente qu'un chiffre faux.

    Args:
        commits: commits normalisés de la fenêtre.
        window_days: profondeur réellement téléchargée.
        now: instant de référence ; maintenant par défaut.
    """
    reference = now or utc_now()
    ordered = sorted(
        commits,
        key=lambda commit: commit.committed_at or datetime.min.replace(tzinfo=reference.tzinfo),
        reverse=True,
    )
    dated = [commit for commit in ordered if commit.committed_at is not None]

    def count_since(days: int) -> int | None:
        if window_days < days:
            return None
        threshold = reference - timedelta(days=days)
        return sum(
            1 for commit in dated if commit.committed_at and commit.committed_at >= threshold
        )

    return Activity(
        window_days=window_days,
        commits=tuple(ordered),
        last_commit_at=dated[0].committed_at if dated else None,
        commits_30_days=count_since(30),
        commits_90_days=count_since(90),
        truncated=len(ordered) >= COMMIT_CEILING,
    )


def collect_activity(client: GitHubClient, full_name: str, *, days: int) -> Activity:
    """Récupère et résume l'activité récente d'un repository.

    Un dépôt vide produit une activité vide : ce n'est pas une erreur.

    Args:
        client: client GitHub authentifié.
        full_name: nom complet du dépôt.
        days: profondeur d'historique à télécharger.
    """
    since = utc_now() - timedelta(days=days)

    try:
        payloads = list(list_commits(client, full_name, since=since))
    except (EmptyRepositoryError, NotFoundError) as exc:
        logger.debug("Activité indisponible pour %s : %s", full_name, exc)
        return Activity(window_days=days)

    commits = [commit for commit in map(normalise_commit, payloads) if commit is not None]
    ignored = len(payloads) - len(commits)
    if ignored:
        logger.warning("%s commit(s) illisible(s) ignoré(s) pour %s.", ignored, full_name)

    activity = summarise(commits, window_days=days)
    if activity.truncated:
        logger.warning(
            "Historique de %s tronqué à %s commits : les décomptes sont des minorants.",
            full_name,
            COMMIT_CEILING,
        )
    return activity
