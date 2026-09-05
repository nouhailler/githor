"""Miroir local des dépôts : ce qui est cloné sur disque, et rien d'autre.

Cette couche ne connaît ni l'API GitHub, ni la base, ni les analyses. Elle
répond à une seule question : « où est la copie locale de ce dépôt, et à quel
commit est-elle ? »
"""

from githor.vcs.git import (
    Checkout,
    GitError,
    GitNotFoundError,
    checkout_path,
    clone_url_for,
    ensure_checkout,
    git_version,
    head_commit,
)

__all__ = [
    "Checkout",
    "GitError",
    "GitNotFoundError",
    "checkout_path",
    "clone_url_for",
    "ensure_checkout",
    "git_version",
    "head_commit",
]
