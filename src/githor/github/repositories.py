"""Accès aux routes GitHub relatives aux repositories.

Ce module se contente d'appeler les bonnes routes et de renvoyer le JSON brut.
Aucune normalisation ici : elle relève de :mod:`githor.collectors.repositories`.
"""

from collections.abc import Iterator
from typing import Any

from githor.github.client import GitHubClient
from githor.logging import get_logger

logger = get_logger("github.repositories")

USER_REPOS_PATH = "/user/repos"


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
