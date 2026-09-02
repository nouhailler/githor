"""Accès à l'utilisateur authentifié."""

from typing import Any

from githor.github.client import GitHubClient
from githor.github.errors import InvalidResponseError

USER_PATH = "/user"


def get_authenticated_user(client: GitHubClient) -> dict[str, Any]:
    """Retourne le profil de l'utilisateur authentifié.

    Raises:
        InvalidResponseError: si la réponse n'est pas un objet exploitable.
    """
    payload = client.get(USER_PATH)
    if not isinstance(payload, dict):
        raise InvalidResponseError("Réponse inattendue de /user : un objet JSON était attendu.")
    return payload


def get_authenticated_login(client: GitHubClient) -> str:
    """Retourne l'identifiant de connexion de l'utilisateur authentifié.

    Raises:
        InvalidResponseError: si le champ ``login`` est absent.
    """
    login = get_authenticated_user(client).get("login")
    if not isinstance(login, str) or not login:
        raise InvalidResponseError("Réponse inattendue de /user : identifiant absent.")
    return login
