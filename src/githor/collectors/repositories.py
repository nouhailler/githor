"""Collecte et normalisation des repositories.

Chaîne de responsabilité :

```text
API GitHub  ->  JSON brut  ->  normalisation  ->  Repository  ->  filtrage
```

La normalisation est explicite, champ par champ : une réponse GitHub qui
changerait de forme doit produire une erreur visible, jamais un silence.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from githor.config import ScanConfig
from githor.github.client import GitHubClient
from githor.github.errors import InvalidResponseError
from githor.github.repositories import list_repositories
from githor.logging import get_logger
from githor.models.repository import Repository
from githor.models.snapshot import RepositorySnapshot
from githor.utils.dates import parse_datetime, utc_now

logger = get_logger("collectors.repositories")


def normalise_repository(payload: dict[str, Any]) -> Repository:
    """Traduit le JSON d'un repository en modèle normalisé.

    Args:
        payload: objet repository renvoyé par l'API GitHub.

    Raises:
        InvalidResponseError: si la charge utile n'a pas la forme attendue
            (champ d'identité manquant, type incohérent, date illisible).
    """
    if not isinstance(payload, dict):
        raise InvalidResponseError("Repository inattendu : un objet JSON était attendu.")

    owner = payload.get("owner")
    licence = payload.get("license")

    try:
        return Repository(
            github_id=payload["id"],
            name=payload["name"],
            full_name=payload["full_name"],
            owner=(owner or {}).get("login", ""),
            description=payload.get("description"),
            html_url=payload.get("html_url", ""),
            clone_url=payload.get("clone_url"),
            ssh_url=payload.get("ssh_url"),
            visibility=_visibility(payload),
            default_branch=payload.get("default_branch") or "main",
            created_at=parse_datetime(payload.get("created_at")),
            updated_at=parse_datetime(payload.get("updated_at")),
            pushed_at=parse_datetime(payload.get("pushed_at")),
            size_kb=payload.get("size") or 0,
            language=payload.get("language"),
            fork=bool(payload.get("fork", False)),
            archived=bool(payload.get("archived", False)),
            disabled=bool(payload.get("disabled", False)),
            has_issues=bool(payload.get("has_issues", False)),
            has_projects=bool(payload.get("has_projects", False)),
            has_wiki=bool(payload.get("has_wiki", False)),
            has_pages=bool(payload.get("has_pages", False)),
            has_discussions=bool(payload.get("has_discussions", False)),
            open_issues_count=payload.get("open_issues_count") or 0,
            stars=payload.get("stargazers_count") or 0,
            forks=payload.get("forks_count") or 0,
            watchers=payload.get("watchers_count") or 0,
            license=(licence or {}).get("spdx_id"),
            topics=tuple(payload.get("topics") or ()),
        )
    except KeyError as exc:
        raise InvalidResponseError(
            f"Repository incomplet renvoyé par GitHub : champ {exc} manquant."
        ) from exc
    except (AttributeError, TypeError, ValueError, ValidationError) as exc:
        identity = payload.get("full_name") or payload.get("id") or "inconnu"
        raise InvalidResponseError(
            f"Repository illisible renvoyé par GitHub ({identity}) : {exc}"
        ) from exc


def _visibility(payload: dict[str, Any]) -> str:
    """Détermine la visibilité, en retombant sur ``private`` si besoin.

    Le champ ``visibility`` distingue public, private et internal ; il est
    absent des réponses les plus anciennes, où seul ``private`` existe.
    """
    visibility = payload.get("visibility")
    if isinstance(visibility, str) and visibility:
        return visibility
    return "private" if payload.get("private") else "public"


def keep_repository(repository: Repository, scan: ScanConfig) -> bool:
    """Indique si un repository entre dans le périmètre configuré."""
    if repository.fork and not scan.include_forks:
        return False
    return not (repository.archived and not scan.include_archived)


@dataclass(frozen=True)
class RepositoryCollection:
    """Repositories retenus, et décompte de ce qui a été écarté.

    Les exclusions sont comptées plutôt que passées sous silence : un dépôt
    absent de la liste doit toujours pouvoir s'expliquer.
    """

    repositories: list[Repository]
    excluded_forks: int = 0
    excluded_archived: int = 0

    @property
    def total_seen(self) -> int:
        """Nombre de repositories renvoyés par GitHub, avant filtrage."""
        return len(self.repositories) + self.excluded_forks + self.excluded_archived


def collect_repositories(client: GitHubClient, scan: ScanConfig) -> RepositoryCollection:
    """Récupère, normalise et filtre les repositories accessibles.

    Args:
        client: client GitHub authentifié.
        scan: périmètre configuré (forks, dépôts archivés).

    Returns:
        Les repositories retenus, triés par nom complet, et les exclusions.
    """
    retained: list[Repository] = []
    excluded_forks = 0
    excluded_archived = 0

    for payload in list_repositories(client):
        repository = normalise_repository(payload)

        if keep_repository(repository, scan):
            retained.append(repository)
            continue

        # Un fork archivé est compté une seule fois, comme fork.
        if repository.fork and not scan.include_forks:
            excluded_forks += 1
            reason = "fork"
        else:
            excluded_archived += 1
            reason = "archivé"
        logger.debug("%s exclu du périmètre (%s).", repository.full_name, reason)

    retained.sort(key=lambda repo: repo.full_name)
    logger.debug(
        "%s repository(s) retenu(s), %s fork(s) et %s archivé(s) exclus.",
        len(retained),
        excluded_forks,
        excluded_archived,
    )
    return RepositoryCollection(retained, excluded_forks, excluded_archived)


def build_snapshot(
    repository: Repository,
    *,
    collected_at: datetime | None = None,
    open_prs: int | None = None,
) -> RepositorySnapshot:
    """Dérive un snapshot des métadonnées d'un repository.

    Les compteurs proviennent de la réponse ``/user/repos``. ``open_issues`` est
    celui de GitHub, qui **inclut les pull requests** ; ``open_prs`` permet de
    les isoler, une fois les issues collectées, sans réécrire le premier.

    Args:
        repository: modèle normalisé.
        collected_at: instant de la mesure ; maintenant par défaut.
        open_prs: pull requests ouvertes ; nul si elles n'ont pas été comptées.
    """
    return RepositorySnapshot(
        collected_at=collected_at or utc_now(),
        stars=repository.stars,
        forks=repository.forks,
        watchers=repository.watchers,
        open_issues=repository.open_issues_count,
        open_prs=open_prs,
        size_kb=repository.size_kb,
        primary_language=repository.language,
        default_branch=repository.default_branch,
    )
