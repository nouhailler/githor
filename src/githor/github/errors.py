"""Erreurs de la couche GitHub.

Toutes dérivent de :class:`~githor.errors.GithorError` : la CLI les présente
comme un message lisible, sans traceback, et le programme sort en erreur.
"""

from datetime import datetime

from githor.errors import GithorError


class GitHubError(GithorError):
    """Erreur générique lors d'un échange avec l'API GitHub."""


class AuthenticationError(GitHubError):
    """Token absent, invalide, révoqué ou expiré (HTTP 401)."""


class PermissionError(GitHubError):
    """Accès refusé : droits insuffisants sur la ressource (HTTP 403)."""


class NotFoundError(GitHubError):
    """Ressource inexistante, supprimée ou invisible pour ce token (HTTP 404)."""


class RateLimitError(GitHubError):
    """Quota d'appels épuisé.

    Githor n'attend jamais la réinitialisation : il s'arrête en indiquant
    l'heure à laquelle le quota redeviendra disponible.
    """

    def __init__(self, message: str, reset_at: datetime | None = None) -> None:
        super().__init__(message)
        self.reset_at = reset_at


class APIUnavailableError(GitHubError):
    """L'API est injoignable ou en erreur serveur, malgré les tentatives."""


class TimeoutError(GitHubError):  # noqa: A001 — nom explicite dans ce contexte
    """Délai d'attente dépassé, malgré les tentatives."""


class InvalidResponseError(GitHubError):
    """Réponse inattendue : JSON illisible ou structure non conforme."""


class UnexpectedStatusError(GitHubError):
    """Code HTTP non prévu par le client."""
