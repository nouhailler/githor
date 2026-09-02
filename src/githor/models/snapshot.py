"""Modèle normalisé d'un snapshot de repository.

Un snapshot est une **mesure datée** : il répond à « où en était ce projet le
2 septembre 2026 ? ». Il n'est jamais mis à jour, seulement ajouté, afin que
l'historique reste intact.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RepositorySnapshot(BaseModel):
    """État mesuré d'un repository à un instant donné."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    collected_at: datetime
    """Instant de la mesure, en UTC."""

    stars: int = 0
    forks: int = 0
    watchers: int = 0

    open_issues: int = 0
    """Compteur GitHub d'issues ouvertes ; il inclut les pull requests."""

    open_prs: int | None = None
    """Nul tant que les pull requests n'ont pas été comptées séparément."""

    size_kb: int = 0
    primary_language: str | None = None
    default_branch: str | None = None


class Language(BaseModel):
    """Part d'un langage dans un repository, à la date du snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    language: str
    bytes: int
    """Valeur brute renvoyée par GitHub, conservée telle quelle."""

    percentage: float
    """Part calculée sur le total des octets, arrondie au dixième."""


class RepositoryFile(BaseModel):
    """Entrée d'arborescence relevée lors d'un snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    type: str
    """``blob`` pour un fichier, ``tree`` pour un répertoire."""

    size: int | None = None
    """Taille en octets ; nulle pour un répertoire."""
