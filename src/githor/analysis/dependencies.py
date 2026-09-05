"""Lecture des dépendances déclarées dans les manifestes d'un dépôt.

Ce sont les dépendances **déclarées**, et rien d'autre : ni celles réellement
installées, ni celles réellement importées. Confondre les trois donnerait un
chiffre commode et faux ; les garder distinctes rend au contraire leurs écarts
lisibles — un paquet déclaré que personne n'importe, un import que nul
manifeste ne déclare.

Chaque dépendance cite le manifeste qui l'a déclarée. Un manifeste illisible est
ignoré sans faire échouer l'analyse : un ``pyproject.toml`` cassé est un
problème du dépôt analysé, pas de Githor.
"""

import json
import re
import tomllib
from configparser import ConfigParser
from configparser import Error as ConfigParserError
from pathlib import Path

from githor.logging import get_logger
from githor.models.code import Dependency, DependencyScope

logger = get_logger("analysis.dependencies")

PYPI = "PyPI"
NPM = "npm"
CRATES = "crates.io"
GO = "Go"

_REQUIREMENT = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?P<extras>\[[^\]]*\])?")
"""Début d'une exigence PEP 508 : le nom, puis d'éventuels extras."""

_GO_REQUIRE = re.compile(r"^(?P<name>[^\s]+)\s+(?P<version>v[^\s]+)")

REQUIREMENTS_GLOBS = ("requirements*.txt", "requirements/*.txt", "requirements/*.in")
"""Emplacements conventionnels des fichiers d'exigences Python."""


def collect_dependencies(root: Path) -> tuple[Dependency, ...]:
    """Relève toutes les dépendances déclarées à la racine d'un dépôt.

    Seule la racine est inspectée, plus le répertoire ``requirements/``. Un
    manifeste enfoui dans un sous-projet appartient à ce sous-projet, et le
    remonter mélangerait des périmètres distincts.

    Args:
        root: racine du miroir.

    Returns:
        Les dépendances trouvées, dédoublonnées et triées par écosystème puis
        par nom.
    """
    found: list[Dependency] = []

    found.extend(_from_pyproject(root / "pyproject.toml"))
    found.extend(_from_setup_cfg(root / "setup.cfg"))
    found.extend(_from_package_json(root / "package.json"))
    found.extend(_from_cargo(root / "Cargo.toml"))
    found.extend(_from_go_mod(root / "go.mod"))

    for pattern in REQUIREMENTS_GLOBS:
        for path in sorted(root.glob(pattern)):
            found.extend(_from_requirements(path, root))

    return _deduplicated(found)


def _deduplicated(dependencies: list[Dependency]) -> tuple[Dependency, ...]:
    """Écarte les doublons stricts et ordonne le résultat.

    Un même paquet peut être déclaré dans deux manifestes — ``pyproject.toml``
    et ``requirements.txt`` en donnent l'exemple courant. Les deux déclarations
    sont conservées si elles diffèrent par leur portée ou leur contrainte : ce
    sont alors deux faits distincts.
    """
    seen: set[tuple[str, str, str, str | None, str]] = set()
    unique: list[Dependency] = []

    for item in dependencies:
        key = (item.ecosystem, item.name.lower(), item.scope, item.specifier, item.source)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    return tuple(sorted(unique, key=lambda item: (item.ecosystem, item.name.lower(), item.source)))


def _read(path: Path) -> str | None:
    """Lit un manifeste, ou renonce sans bruit s'il est absent ou illisible."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.debug("Manifeste illisible : %s (%s)", path, exc)
        return None


def parse_requirement(
    line: str, *, source: str, scope: DependencyScope, group: str | None = None
) -> Dependency | None:
    """Traduit une exigence PEP 508 en dépendance.

    Args:
        line: exigence, telle qu'écrite dans le manifeste.
        source: chemin du manifeste, relatif à la racine.
        scope: rôle de la dépendance.
        group: extra ou groupe dont elle relève, le cas échéant.

    Returns:
        La dépendance, ou ``None`` si la ligne n'en décrit pas une.
    """
    # Le marqueur d'environnement ne fait pas partie du nom ni de la contrainte.
    cleaned = line.split(";", 1)[0].split("#", 1)[0].strip()
    if not cleaned:
        return None

    match = _REQUIREMENT.match(cleaned)
    if match is None:
        return None

    name = match.group("name")
    specifier = cleaned[match.end() :].strip() or None
    return Dependency(
        name=name,
        ecosystem=PYPI,
        scope=scope,
        specifier=specifier,
        source=source,
        group=group,
    )


def _from_pyproject(path: Path) -> list[Dependency]:
    """Lit un ``pyproject.toml``, qu'il suive PEP 621, PEP 735 ou Poetry."""
    content = _read(path)
    if content is None:
        return []

    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        logger.debug("pyproject.toml invalide : %s", exc)
        return []

    source = path.name
    found: list[Dependency] = []

    project = data.get("project")
    if isinstance(project, dict):
        found.extend(
            item
            for line in _strings(project.get("dependencies"))
            if (item := parse_requirement(line, source=source, scope=DependencyScope.RUNTIME))
        )
        extras = project.get("optional-dependencies")
        if isinstance(extras, dict):
            for name, entries in extras.items():
                found.extend(
                    item
                    for line in _strings(entries)
                    if (
                        item := parse_requirement(
                            line, source=source, scope=DependencyScope.OPTIONAL, group=str(name)
                        )
                    )
                )

    # PEP 735 : groupes de dépendances, qui ne s'installent jamais avec le paquet.
    groups = data.get("dependency-groups")
    if isinstance(groups, dict):
        for name, entries in groups.items():
            found.extend(
                item
                for line in _strings(entries)
                if (
                    item := parse_requirement(
                        line, source=source, scope=DependencyScope.DEVELOPMENT, group=str(name)
                    )
                )
            )

    found.extend(_from_poetry(data, source=source))
    return found


def _from_poetry(data: dict[str, object], *, source: str) -> list[Dependency]:
    """Lit les dépendances au format Poetry, qui les écrit en tables."""
    tool = data.get("tool")
    poetry = tool.get("poetry") if isinstance(tool, dict) else None
    if not isinstance(poetry, dict):
        return []

    found: list[Dependency] = []
    found.extend(
        _poetry_table(poetry.get("dependencies"), source=source, scope=DependencyScope.RUNTIME)
    )

    groups = poetry.get("group")
    if isinstance(groups, dict):
        for name, definition in groups.items():
            if isinstance(definition, dict):
                found.extend(
                    _poetry_table(
                        definition.get("dependencies"),
                        source=source,
                        scope=DependencyScope.DEVELOPMENT,
                        group=str(name),
                    )
                )

    return found


def _poetry_table(
    table: object, *, source: str, scope: DependencyScope, group: str | None = None
) -> list[Dependency]:
    """Traduit une table Poetry ``nom = contrainte`` en dépendances."""
    if not isinstance(table, dict):
        return []

    found: list[Dependency] = []
    for name, constraint in table.items():
        # « python » y désigne l'interpréteur, pas un paquet installable.
        if str(name).lower() == "python":
            continue
        found.append(
            Dependency(
                name=str(name),
                ecosystem=PYPI,
                scope=scope,
                specifier=_constraint(constraint),
                source=source,
                group=group,
            )
        )
    return found


def _constraint(value: object) -> str | None:
    """Extrait la contrainte de version d'une déclaration Poetry ou Cargo."""
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        version = value.get("version")
        return str(version) if isinstance(version, str) and version else None
    return None


def _from_setup_cfg(path: Path) -> list[Dependency]:
    """Lit ``install_requires`` d'un ``setup.cfg``."""
    content = _read(path)
    if content is None:
        return []

    parser = ConfigParser()
    try:
        parser.read_string(content)
    except ConfigParserError as exc:
        logger.debug("setup.cfg invalide : %s", exc)
        return []

    if not parser.has_option("options", "install_requires"):
        return []

    lines = parser.get("options", "install_requires").splitlines()
    return [
        item
        for line in lines
        if (item := parse_requirement(line, source=path.name, scope=DependencyScope.RUNTIME))
    ]


def _from_requirements(path: Path, root: Path) -> list[Dependency]:
    """Lit un fichier d'exigences Python.

    Les directives commençant par un tiret — ``-r autre.txt``, ``-e .``,
    ``--index-url`` — ne sont pas des dépendances : le fichier qu'elles
    référencent est lu pour lui-même s'il existe.
    """
    content = _read(path)
    if content is None:
        return []

    source = path.relative_to(root).as_posix()
    # Un fichier nommé « dev », « test » ou « lint » déclare de l'outillage.
    lowered = source.lower()
    scope = (
        DependencyScope.DEVELOPMENT
        if any(word in lowered for word in ("dev", "test", "lint", "doc"))
        else DependencyScope.RUNTIME
    )

    return [
        item
        for line in content.splitlines()
        if not line.strip().startswith("-")
        if (item := parse_requirement(line, source=source, scope=scope))
    ]


def _from_package_json(path: Path) -> list[Dependency]:
    """Lit les dépendances d'un ``package.json``."""
    content = _read(path)
    if content is None:
        return []

    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        logger.debug("package.json invalide : %s", exc)
        return []

    if not isinstance(data, dict):
        return []

    sections = {
        "dependencies": DependencyScope.RUNTIME,
        "devDependencies": DependencyScope.DEVELOPMENT,
        "peerDependencies": DependencyScope.RUNTIME,
        "optionalDependencies": DependencyScope.OPTIONAL,
    }

    found: list[Dependency] = []
    for section, scope in sections.items():
        table = data.get(section)
        if not isinstance(table, dict):
            continue
        found.extend(
            Dependency(
                name=str(name),
                ecosystem=NPM,
                scope=scope,
                specifier=str(version) or None,
                source=path.name,
                group=section if section != "dependencies" else None,
            )
            for name, version in table.items()
        )

    return found


def _from_cargo(path: Path) -> list[Dependency]:
    """Lit les dépendances d'un ``Cargo.toml``."""
    content = _read(path)
    if content is None:
        return []

    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        logger.debug("Cargo.toml invalide : %s", exc)
        return []

    sections = {
        "dependencies": DependencyScope.RUNTIME,
        "dev-dependencies": DependencyScope.DEVELOPMENT,
        "build-dependencies": DependencyScope.DEVELOPMENT,
    }

    found: list[Dependency] = []
    for section, scope in sections.items():
        table = data.get(section)
        if not isinstance(table, dict):
            continue
        found.extend(
            Dependency(
                name=str(name),
                ecosystem=CRATES,
                scope=scope,
                specifier=_constraint(constraint),
                source=path.name,
                group=section if section != "dependencies" else None,
            )
            for name, constraint in table.items()
        )

    return found


def _from_go_mod(path: Path) -> list[Dependency]:
    """Lit les modules requis par un ``go.mod``, blocs ``require`` compris."""
    content = _read(path)
    if content is None:
        return []

    found: list[Dependency] = []
    in_block = False

    for raw in content.splitlines():
        line = raw.split("//", 1)[0].strip()
        if not line:
            continue

        if line.startswith("require ("):
            in_block = True
            continue
        if in_block and line == ")":
            in_block = False
            continue

        candidate = (
            line[len("require ") :].strip()
            if line.startswith("require ")
            else (line if in_block else "")
        )
        match = _GO_REQUIRE.match(candidate)
        if match is not None:
            found.append(
                Dependency(
                    name=match.group("name"),
                    ecosystem=GO,
                    scope=DependencyScope.RUNTIME,
                    specifier=match.group("version"),
                    source=path.name,
                )
            )

    return found


def _strings(value: object) -> list[str]:
    """Retient les seules chaînes d'une liste hétérogène issue d'un TOML."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
