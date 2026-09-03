"""Collecte des releases d'un repository.

Beaucoup de dépôts n'ont aucune release : une liste vide est un résultat, pas
une erreur.
"""

from typing import Any

from githor.github.client import GitHubClient
from githor.github.errors import EmptyRepositoryError, NotFoundError
from githor.github.repositories import list_releases
from githor.logging import get_logger
from githor.models.release import Release
from githor.utils.dates import parse_datetime

logger = get_logger("collectors.releases")


def normalise_release(payload: dict[str, Any]) -> Release | None:
    """Traduit une release GitHub en modèle normalisé.

    Returns:
        ``None`` si la charge utile est inexploitable : une release illisible ne
        doit pas interrompre la collecte de tout un dépôt.
    """
    tag = payload.get("tag_name")
    if not isinstance(tag, str) or not tag:
        return None

    try:
        published_at = parse_datetime(payload.get("published_at"))
    except ValueError:
        published_at = None

    name = payload.get("name")
    return Release(
        tag=tag,
        name=name if isinstance(name, str) else None,
        published_at=published_at,
        draft=bool(payload.get("draft")),
        prerelease=bool(payload.get("prerelease")),
    )


def collect_releases(client: GitHubClient, full_name: str) -> list[Release]:
    """Récupère et normalise les releases d'un repository."""
    try:
        payloads = list(list_releases(client, full_name))
    except (EmptyRepositoryError, NotFoundError) as exc:
        logger.debug("Releases indisponibles pour %s : %s", full_name, exc)
        return []

    releases = [release for release in map(normalise_release, payloads) if release is not None]
    ignored = len(payloads) - len(releases)
    if ignored:
        logger.warning("%s release(s) illisible(s) ignorée(s) pour %s.", ignored, full_name)
    return releases
