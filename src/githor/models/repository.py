"""Modèle normalisé d'un repository GitHub.

Ce modèle est indépendant de la forme des réponses de l'API : la traduction
depuis le JSON GitHub est la responsabilité de :mod:`githor.collectors`.
Il décrit le projet lui-même, pas son état à un instant donné — ce dernier
relève du snapshot (étape 8).
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class Repository(BaseModel):
    """Métadonnées d'un repository, telles que Githor les conserve."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # ── Identité ─────────────────────────────────────────────────────────────
    github_id: int
    """Identifiant numérique GitHub, stable même après un renommage."""

    name: str
    full_name: str
    owner: str
    description: str | None = None
    homepage: str | None = None

    # ── Adresses ─────────────────────────────────────────────────────────────
    html_url: str
    clone_url: str | None = None
    ssh_url: str | None = None

    # ── Nature ───────────────────────────────────────────────────────────────
    visibility: str = "public"
    default_branch: str = "main"

    # ── Dates ────────────────────────────────────────────────────────────────
    created_at: datetime | None = None
    updated_at: datetime | None = None
    pushed_at: datetime | None = None
    """Date du dernier push ; nulle pour un dépôt encore vide."""

    # ── Contenu ──────────────────────────────────────────────────────────────
    size_kb: int = 0
    """Taille annoncée par GitHub, en kilo-octets."""

    language: str | None = None

    # ── Statut ───────────────────────────────────────────────────────────────
    fork: bool = False
    archived: bool = False
    disabled: bool = False

    # ── Fonctionnalités activées ─────────────────────────────────────────────
    has_issues: bool = False
    has_projects: bool = False
    has_wiki: bool = False
    has_pages: bool = False
    has_discussions: bool = False

    # ── Compteurs ────────────────────────────────────────────────────────────
    open_issues_count: int = 0
    stars: int = 0
    forks: int = 0
    watchers: int = 0
    """Abonnés déclarés par ``watchers_count`` ; GitHub y recopie les étoiles."""

    # ── Classification ───────────────────────────────────────────────────────
    license: str | None = None
    """Identifiant SPDX de la licence, par exemple ``MIT``."""

    topics: tuple[str, ...] = ()
