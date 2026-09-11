"""Collecte de l'arborescence d'un repository et détection des éléments attendus.

Le V0.1 ne lit aucun contenu de fichier : il s'intéresse à ce qui est présent.
La détection est purement déclarative — un marqueur est une liste de chemins ou
un préfixe de répertoire — afin qu'un constat puisse toujours nommer le fichier
qui l'a motivé.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from githor.github.client import GitHubClient
from githor.github.errors import EmptyRepositoryError, NotFoundError
from githor.github.repositories import get_tree
from githor.logging import get_logger
from githor.models.repository import Repository
from githor.models.snapshot import RepositoryFile

logger = get_logger("collectors.structure")

FILE_MARKERS: dict[str, tuple[str, ...]] = {
    "readme": ("readme.md", "readme", "readme.rst", "readme.txt"),
    "license": ("license", "license.md", "license.txt", "copying", "copying.md"),
    "changelog": ("changelog.md", "changelog", "changelog.txt"),
    "contributing": ("contributing.md", "contributing"),
    "code_of_conduct": ("code_of_conduct.md", ".github/code_of_conduct.md"),
    "security": ("security.md", ".github/security.md"),
    "dockerfile": ("dockerfile", "containerfile"),
    "compose": (
        "compose.yaml",
        "compose.yml",
        "docker-compose.yml",
        "docker-compose.yaml",
    ),
    "dependabot": (".github/dependabot.yml", ".github/dependabot.yaml"),
    "package_json": ("package.json",),
    "package_lock": ("package-lock.json", "pnpm-lock.yaml", "yarn.lock"),
    "pyproject": ("pyproject.toml",),
    "requirements": ("requirements.txt",),
    "editorconfig": (".editorconfig",),
    "gitignore": (".gitignore",),
    "legal_notice": ("mentions-legales.html", "mentions_legales.html", "legal.html"),
    "about_page": ("a-propos.html", "a_propos.html", "about.html"),
    "index_html": ("index.html",),
}
"""Marqueurs identifiés par un chemin exact, comparé sans tenir compte de la casse."""

DIRECTORY_MARKERS: dict[str, tuple[str, ...]] = {
    "docs": ("docs", "doc"),
    "tests": ("tests", "test", "spec"),
    "src": ("src",),
    "github": (".github",),
    "github_workflows": (".github/workflows",),
}
"""Marqueurs identifiés par un répertoire, présent dès qu'il contient un fichier."""


@dataclass(frozen=True)
class Structure:
    """Arborescence relevée et marqueurs détectés."""

    files: list[RepositoryFile] = field(default_factory=list)
    truncated: bool = False
    """Vrai si GitHub a tronqué l'arborescence : les décomptes sont des minorants."""

    @property
    def file_count(self) -> int:
        """Nombre de fichiers."""
        return sum(1 for entry in self.files if entry.type == "blob")

    @property
    def directory_count(self) -> int:
        """Nombre de répertoires."""
        return sum(1 for entry in self.files if entry.type == "tree")

    @property
    def markers(self) -> dict[str, str | None]:
        """Marqueur -> chemin qui l'a satisfait, ou ``None`` s'il est absent."""
        return detect_markers(entry.path for entry in self.files)


def detect_markers(paths: Iterable[str]) -> dict[str, str | None]:
    """Associe chaque marqueur au chemin qui le satisfait, ou à ``None``.

    Conserver le chemin trouvé, et non un simple booléen, permet à un constat
    d'expliquer sur quoi il se fonde.

    Args:
        paths: chemins de l'arborescence, tels que renvoyés par GitHub.
    """
    found: dict[str, str | None] = dict.fromkeys(FILE_MARKERS)
    found.update(dict.fromkeys(DIRECTORY_MARKERS))

    for path in paths:
        lowered = path.lower()

        for marker, candidates in FILE_MARKERS.items():
            if found[marker] is None and lowered in candidates:
                found[marker] = path

        for marker, directories in DIRECTORY_MARKERS.items():
            if found[marker] is not None:
                continue
            if any(lowered == d or lowered.startswith(f"{d}/") for d in directories):
                found[marker] = path

    return found


def normalise_tree(payload: dict[str, Any]) -> Structure:
    """Traduit la réponse ``git/trees`` en arborescence normalisée."""
    entries = payload.get("tree")
    if not isinstance(entries, list):
        return Structure()

    files = [
        RepositoryFile(
            path=str(entry["path"]),
            type=str(entry.get("type", "blob")),
            size=entry.get("size") if isinstance(entry.get("size"), int) else None,
        )
        for entry in entries
        if isinstance(entry, dict) and entry.get("path")
    ]
    return Structure(files=files, truncated=bool(payload.get("truncated")))


def collect_structure(client: GitHubClient, repository: Repository) -> Structure:
    """Récupère l'arborescence d'un repository sur sa branche par défaut.

    Un dépôt vide produit une arborescence vide : ce n'est pas une erreur.
    """
    try:
        payload = get_tree(client, repository.full_name, repository.default_branch)
    except (EmptyRepositoryError, NotFoundError) as exc:
        logger.debug("Arborescence indisponible pour %s : %s", repository.full_name, exc)
        return Structure()

    structure = normalise_tree(payload)
    if structure.truncated:
        logger.warning(
            "Arborescence de %s tronquée par GitHub : les décomptes sont des minorants.",
            repository.full_name,
        )
    return structure
