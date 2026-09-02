"""Accès aux routes GitHub relatives aux repositories.

Ce module se contente d'appeler les bonnes routes et de renvoyer le JSON brut.
Aucune normalisation ici : elle relève de :mod:`githor.collectors.repositories`.
"""

from collections.abc import Iterator
from datetime import datetime
from typing import Any

from githor.github.client import GitHubClient
from githor.github.errors import InvalidResponseError
from githor.logging import get_logger

logger = get_logger("github.repositories")

USER_REPOS_PATH = "/user/repos"
REPOSITORY_PATH = "/repos/{full_name}"
LANGUAGES_PATH = "/repos/{full_name}/languages"
TREE_PATH = "/repos/{full_name}/git/trees/{ref}"
COMMITS_PATH = "/repos/{full_name}/commits"

MAX_COMMIT_PAGES = 10
"""Borne de sécurité : 1 000 commits suffisent à mesurer une activité récente."""


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


def get_languages(client: GitHubClient, full_name: str) -> dict[str, int]:
    """Récupère la répartition des langages, en octets.

    Returns:
        Un dictionnaire langage -> octets ; vide pour un dépôt sans code reconnu.

    Raises:
        InvalidResponseError: si la réponse n'est pas un objet JSON.
    """
    payload = client.get(LANGUAGES_PATH.format(full_name=full_name))
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise InvalidResponseError(f"Langages inattendus pour {full_name} : objet JSON attendu.")
    return payload


def get_tree(client: GitHubClient, full_name: str, ref: str) -> dict[str, Any]:
    """Récupère l'arborescence complète d'une référence, en un seul appel.

    L'API récursive évite un appel par répertoire. GitHub tronque au-delà d'une
    certaine taille et le signale par ``truncated``.

    Raises:
        EmptyRepositoryError: si le dépôt n'a pas d'historique.
        NotFoundError: si la référence n'existe pas.
        InvalidResponseError: si la réponse n'est pas un objet JSON.
    """
    payload = client.get(TREE_PATH.format(full_name=full_name, ref=ref), params={"recursive": "1"})
    if not isinstance(payload, dict):
        raise InvalidResponseError(
            f"Arborescence inattendue pour {full_name}@{ref} : objet JSON attendu."
        )
    return payload


def list_commits(
    client: GitHubClient, full_name: str, *, since: datetime
) -> Iterator[dict[str, Any]]:
    """Parcourt les commits postérieurs à une date.

    Seule la fenêtre demandée est téléchargée : l'historique complet d'un dépôt
    n'a pas à transiter pour mesurer son activité récente.

    Raises:
        EmptyRepositoryError: si le dépôt n'a pas d'historique.
    """
    yield from client.get_paginated(
        COMMITS_PATH.format(full_name=full_name),
        params={"since": since.isoformat().replace("+00:00", "Z")},
        max_pages=MAX_COMMIT_PAGES,
    )
