"""Exceptions applicatives de Githor.

Toutes les erreurs attendues dérivent de :class:`GithorError`. La CLI les
affiche telles quelles à l'utilisateur, sans traceback : leur message doit
donc rester compréhensible sans connaître le code.
"""


class GithorError(Exception):
    """Erreur attendue, dont le message est destiné à l'utilisateur final."""


class ConfigError(GithorError):
    """Configuration absente, illisible ou invalide."""


class StorageError(GithorError):
    """Base SQLite inaccessible, verrouillée ou corrompue."""
