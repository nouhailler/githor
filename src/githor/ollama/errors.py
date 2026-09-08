"""Erreurs de la couche Ollama.

Toutes dérivent de :class:`~githor.errors.GithorError` : la CLI les présente
comme un message lisible, sans traceback, et le programme sort en erreur.
"""

from githor.errors import GithorError


class OllamaError(GithorError):
    """Erreur générique lors d'un échange avec Ollama."""


class OllamaUnavailableError(OllamaError):
    """Le serveur Ollama ne répond pas : il n'est probablement pas démarré."""


class ModelNotFoundError(OllamaError):
    """Le modèle demandé n'est pas présent sur le serveur Ollama."""


class OllamaTimeoutError(OllamaError):
    """Délai d'attente dépassé : l'inférence locale n'a pas répondu à temps."""


class InvalidResponseError(OllamaError):
    """Réponse inattendue : JSON illisible ou structure non conforme."""
