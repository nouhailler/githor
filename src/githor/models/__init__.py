"""Modèles normalisés (repository, snapshot, activité, findings)."""

from githor.models.repository import Repository
from githor.models.snapshot import RepositorySnapshot

__all__ = ["Repository", "RepositorySnapshot"]
