"""Couche d'accès à un serveur Ollama local.

Cette couche ne connaît ni la base de données, ni la CLI : elle expose
uniquement un client HTTP et les erreurs associées. Contrairement à
``github``, elle ne joint aucun service tiers — Ollama tourne sur la machine
de l'utilisateur.
"""

from githor.ollama.client import DEFAULT_HOST, OllamaClient
from githor.ollama.errors import OllamaError

__all__ = ["DEFAULT_HOST", "OllamaClient", "OllamaError"]
