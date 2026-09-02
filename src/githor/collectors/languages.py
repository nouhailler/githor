"""Collecte des langages d'un repository.

Les octets bruts renvoyés par GitHub sont conservés tels quels ; le pourcentage
est calculé et stocké à côté, pour n'avoir pas à le recalculer à chaque lecture.
"""

from githor.github.client import GitHubClient
from githor.github.errors import EmptyRepositoryError, NotFoundError
from githor.github.repositories import get_languages
from githor.logging import get_logger
from githor.models.snapshot import Language

logger = get_logger("collectors.languages")


def compute_breakdown(raw: dict[str, int]) -> list[Language]:
    """Transforme une répartition brute en langages ordonnés par poids.

    Args:
        raw: dictionnaire langage -> octets, tel que renvoyé par GitHub.

    Returns:
        Les langages du plus lourd au plus léger ; liste vide si le total est nul.
    """
    counted = {
        language: int(size)
        for language, size in raw.items()
        if isinstance(size, int | float) and size > 0
    }
    total = sum(counted.values())
    if total == 0:
        return []

    return [
        Language(
            language=language,
            bytes=size,
            percentage=round(size * 100 / total, 1),
        )
        for language, size in sorted(counted.items(), key=lambda item: (-item[1], item[0]))
    ]


def collect_languages(client: GitHubClient, full_name: str) -> list[Language]:
    """Récupère et normalise les langages d'un repository.

    Un dépôt vide ou sans code reconnu produit une liste vide : ce n'est pas
    une erreur.
    """
    try:
        raw = get_languages(client, full_name)
    except (EmptyRepositoryError, NotFoundError) as exc:
        logger.debug("Langages indisponibles pour %s : %s", full_name, exc)
        return []
    return compute_breakdown(raw)
