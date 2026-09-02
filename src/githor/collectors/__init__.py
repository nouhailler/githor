"""Collectors : transforment les réponses GitHub en modèles normalisés."""

from githor.collectors.activity import collect_activity
from githor.collectors.languages import collect_languages
from githor.collectors.repositories import (
    RepositoryCollection,
    build_snapshot,
    collect_repositories,
    normalise_repository,
)
from githor.collectors.structure import Structure, collect_structure, detect_markers

__all__ = [
    "RepositoryCollection",
    "Structure",
    "build_snapshot",
    "collect_activity",
    "collect_languages",
    "collect_repositories",
    "collect_structure",
    "detect_markers",
    "normalise_repository",
]
