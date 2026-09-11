"""Accès aux routes GitHub relatives aux repositories.

Ce module se contente d'appeler les bonnes routes et de renvoyer le JSON brut.
Aucune normalisation ici : elle relève de :mod:`githor.collectors.repositories`.
"""

import base64
import binascii
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from githor.github.client import GitHubClient
from githor.github.errors import InvalidResponseError, NotFoundError
from githor.logging import get_logger

logger = get_logger("github.repositories")

USER_REPOS_PATH = "/user/repos"
REPOSITORY_PATH = "/repos/{full_name}"
LANGUAGES_PATH = "/repos/{full_name}/languages"
TREE_PATH = "/repos/{full_name}/git/trees/{ref}"
CONTENT_PATH = "/repos/{full_name}/contents/{path}"
COMMITS_PATH = "/repos/{full_name}/commits"
RELEASES_PATH = "/repos/{full_name}/releases"
ISSUES_PATH = "/repos/{full_name}/issues"

MAX_COMMIT_PAGES = 10
"""Borne de sécurité : 1 000 commits suffisent à mesurer une activité récente."""

MAX_RELEASE_PAGES = 3
"""Borne de sécurité : 300 releases suffisent à juger de la maturité d'un dépôt."""

MAX_ISSUE_PAGES = 5
"""Borne de sécurité : 500 issues suffisent aux métriques du V0.1."""


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


def get_content(client: GitHubClient, full_name: str, path: str, ref: str) -> str | None:
    """Récupère le contenu textuel d'un fichier, ou ``None`` s'il est inexploitable.

    Exception bornée à l'invariant du V0.1 (« un marqueur ne lit qu'un nom,
    jamais un contenu ») : réservée aux quelques fichiers déjà repérés par
    marqueur (cf. :mod:`githor.collectors.content`), jamais à l'arborescence
    entière. GitHub encode le contenu en base64 pour les fichiers de moins
    d'1 Mo ; un fichier introuvable, un répertoire ou un contenu binaire ne
    sont pas des échecs de scan, seulement l'absence d'un signal.
    """
    try:
        payload = client.get(
            CONTENT_PATH.format(full_name=full_name, path=path), params={"ref": ref}
        )
    except NotFoundError:
        return None

    if not isinstance(payload, dict) or payload.get("encoding") != "base64":
        return None

    try:
        return base64.b64decode(payload.get("content", "")).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None


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


def list_releases(client: GitHubClient, full_name: str) -> Iterator[dict[str, Any]]:
    """Parcourt les releases d'un repository, de la plus récente à la plus ancienne.

    Les brouillons ne sont visibles que des utilisateurs ayant les droits
    d'écriture ; leur absence n'est pas une anomalie.
    """
    yield from client.get_paginated(
        RELEASES_PATH.format(full_name=full_name), max_pages=MAX_RELEASE_PAGES
    )


def list_issues(client: GitHubClient, full_name: str) -> Iterator[dict[str, Any]]:
    """Parcourt les issues d'un repository, ouvertes comme fermées.

    GitHub range les pull requests parmi les issues : le tri des unes et des
    autres relève de :mod:`githor.collectors.issues`, qui dispose de la charge
    utile complète.
    """
    yield from client.get_paginated(
        ISSUES_PATH.format(full_name=full_name),
        params={"state": "all", "sort": "created", "direction": "desc"},
        max_pages=MAX_ISSUE_PAGES,
    )
