"""Collectors : transforment les réponses GitHub en modèles normalisés."""

from githor.collectors.repositories import (
    RepositoryCollection,
    collect_repositories,
    normalise_repository,
)

__all__ = ["RepositoryCollection", "collect_repositories", "normalise_repository"]
