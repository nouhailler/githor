"""Accès aux routes GitHub relatives aux repositories.

Ce module se contente d'appeler les bonnes routes et de renvoyer le JSON brut.
Aucune normalisation ici : elle relève de :mod:`githor.collectors.repositories`.
"""

from collections.abc import Iterator
from typing import Any

from githor.github.client import GitHubClient
from githor.github.errors import InvalidResponseError
from githor.logging import get_logger

logger = get_logger("github.repositories")

USER_REPOS_PATH = "/user/repos"
REPOSITORY_PATH = "/repos/{full_name}"


def list_repositories(client: GitHubClient) -> Iterator[dict[str, Any]]:
    """Parcourt tous les repositories accessibles à l'utilisateur authentifié.

    Couvre les dépôts possédés, ceux où l'utilisateur est collaborateur et ceux
    de ses organisations : c'est le comportement par défaut de ``/user/repos``.
    Le tri par nom complet rend l'ordre reproductible d'un scan à l'autre.

    Yields:
        Le JSON brut de chaque repository, tel que renvoyé par GitHub.
    """
    logger.debug("Récupération des repositories via %s", USER_REPOS_PATH)
    yield from client.get_paginated(
        USER_REPOS_PATH,
        params={"sort": "full_name", "direction": "asc"},
    )


def get_repository(client: GitHubClient, full_name: str) -> dict[str, Any]:
    """Récupère un repository unique.

    Args:
        client: client GitHub authentifié.
        full_name: nom complet, sous la forme ``propriétaire/dépôt``.

    Raises:
        NotFoundError: si le dépôt n'existe pas, a été supprimé, ou reste
            invisible pour ce jeton.
        InvalidResponseError: si la réponse n'est pas un objet JSON.
    """
    logger.debug("Récupération du repository %s", full_name)
    payload = client.get(REPOSITORY_PATH.format(full_name=full_name))
    if not isinstance(payload, dict):
        raise InvalidResponseError(f"Réponse inattendue pour {full_name} : objet JSON attendu.")
    return payload
