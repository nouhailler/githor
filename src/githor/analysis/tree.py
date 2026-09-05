"""Parcours de l'arborescence d'un miroir : ce qui est lu, ce qui est écarté.

Trois familles de fichiers ne sont pas analysées, et chacune est **comptée** :
un fichier écarté en silence fausserait tous les décomptes sans qu'on puisse le
savoir.

- ceux qui pendent d'un répertoire d'artefacts (``node_modules``, ``dist``,
  ``.venv``…). La liste est déclarative et volontairement conventionnelle : ces
  noms désignent partout des produits de construction ou des dépendances
  installées, jamais du code écrit ici ;
- ceux qui contiennent un octet nul dans leurs premiers kilo-octets — image,
  archive, exécutable. Compter leurs « lignes » n'aurait aucun sens ;
- ceux qui dépassent ``audit.max_file_bytes``. Au-delà d'un mégaoctet, un
  fichier est un minifié, une donnée embarquée ou un artefact : l'analyser
  fausserait toutes les moyennes.

Les liens symboliques ne sont jamais suivis : ils permettraient à un dépôt de
faire sortir l'analyse de son propre miroir.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from githor.logging import get_logger

logger = get_logger("analysis.tree")

EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".idea",
        ".vscode",
        ".gradle",
        ".tox",
        ".nox",
        ".venv",
        "venv",
        ".eggs",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".cache",
        "__pycache__",
        "node_modules",
        "bower_components",
        "vendor",
        "site-packages",
        "dist",
        "build",
        "target",
        "out",
        ".next",
        ".nuxt",
        ".svelte-kit",
        "coverage",
        "htmlcov",
        "Pods",
    }
)
"""Répertoires dont le contenu n'est pas du code écrit dans ce dépôt."""

EXCLUDED_SUFFIXES = frozenset({".egg-info", ".dist-info"})
"""Suffixes de répertoires générés, dont le nom varie avec le projet."""

BINARY_PROBE_BYTES = 8192
"""Taille de l'échantillon lu pour décider si un fichier est binaire."""


@dataclass(frozen=True)
class SourceFile:
    """Un fichier rencontré lors du parcours, et ce qu'on a décidé d'en faire."""

    path: Path
    """Chemin absolu sur disque."""

    relative: str
    """Chemin relatif à la racine du dépôt, en séparateurs POSIX."""

    size: int
    binary: bool = False
    too_large: bool = False

    @property
    def readable(self) -> bool:
        """Vrai si le contenu peut être lu et compté."""
        return not self.binary and not self.too_large


def is_excluded_directory(name: str) -> bool:
    """Dit si un répertoire doit être écarté du parcours."""
    return name in EXCLUDED_DIRECTORIES or any(
        name.endswith(suffix) for suffix in EXCLUDED_SUFFIXES
    )


def looks_binary(path: Path) -> bool:
    """Devine si un fichier est binaire à la présence d'un octet nul.

    C'est l'heuristique de ``git`` lui-même : simple, rapide, et sans faux
    positif sur du texte réel, qui ne contient jamais d'octet nul.
    """
    try:
        with path.open("rb") as handle:
            return b"\x00" in handle.read(BINARY_PROBE_BYTES)
    except OSError as exc:
        logger.debug("Fichier illisible, traité comme binaire : %s (%s)", path, exc)
        return True


def walk_files(root: Path, *, max_bytes: int) -> Iterator[SourceFile]:
    """Parcourt les fichiers d'un dépôt, exclusions appliquées.

    Args:
        root: racine du miroir.
        max_bytes: taille au-delà de laquelle un fichier est compté sans être lu.

    Yields:
        Chaque fichier rencontré, qu'il soit analysable ou non.
    """
    for entry in sorted(root.rglob("*")):
        if entry.is_symlink():
            continue
        if not entry.is_file():
            continue

        relative = entry.relative_to(root)
        if any(is_excluded_directory(part) for part in relative.parts[:-1]):
            continue

        try:
            size = entry.stat().st_size
        except OSError as exc:
            logger.debug("Taille illisible, fichier ignoré : %s (%s)", entry, exc)
            continue

        if size > max_bytes:
            yield SourceFile(path=entry, relative=relative.as_posix(), size=size, too_large=True)
            continue

        yield SourceFile(
            path=entry,
            relative=relative.as_posix(),
            size=size,
            binary=looks_binary(entry),
        )


def read_text(path: Path) -> str:
    """Lit un fichier texte sans jamais échouer sur son encodage.

    Les dépôts anciens mêlent UTF-8 et encodages régionaux. Un octet indécodable
    devient un caractère de remplacement : cela n'affecte ni le nombre de lignes
    ni la reconnaissance des commentaires, et évite qu'un fichier exotique fasse
    échouer l'analyse de tout un dépôt.
    """
    return path.read_text(encoding="utf-8", errors="replace")


def local_roots(root: Path) -> frozenset[str]:
    """Modules et paquets Python de premier niveau appartenant au dépôt.

    Sert à distinguer ``import githor.config`` — le dépôt s'importe lui-même —
    d'une vraie dépendance. La disposition ``src/`` est prise en compte, faute
    de quoi tout projet moderne verrait ses propres modules comptés comme
    tierce partie.
    """
    found: set[str] = set()

    for base in (root, root / "src"):
        if not base.is_dir():
            continue
        for entry in base.iterdir():
            if entry.is_symlink():
                continue
            if entry.is_dir() and not is_excluded_directory(entry.name):
                if (entry / "__init__.py").exists() or any(entry.glob("*.py")):
                    found.add(entry.name)
            elif entry.is_file() and entry.suffix == ".py":
                found.add(entry.stem)

    return frozenset(found)
