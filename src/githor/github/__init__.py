"""Couche d'accès à l'API GitHub.

Cette couche ne connaît ni la base de données, ni la CLI : elle expose
uniquement un client HTTP et les erreurs associées.
"""

from githor.github.client import DEFAULT_API_URL, GitHubClient, RateLimit
from githor.github.errors import GitHubError

__all__ = ["DEFAULT_API_URL", "GitHubClient", "GitHubError", "RateLimit"]
