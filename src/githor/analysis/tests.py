"""Détection des tests présents dans un dépôt.

La détection est **structurelle** : conventions de nommage pour les fichiers,
AST pour les fonctions Python, imports et dépendances déclarées pour les cadres.
Rien n'est exécuté — Githor ne lance jamais les tests d'un dépôt qu'il analyse,
pas plus qu'il n'écrit sur GitHub.

Ce que la V0.1 savait dire était « un répertoire nommé tests existe ». Ce module
répond à la question suivante : combien de fichiers, combien de fonctions, et
avec quel outil.
"""

from collections.abc import Iterable
from pathlib import PurePosixPath

from githor.models.code import Dependency, ModuleAnalysis, TestSuite

TEST_DIRECTORIES = frozenset({"tests", "test", "spec", "specs", "__tests__", "testing"})
"""Répertoires dont le contenu est, par convention, du test."""

TEST_PREFIXES = ("test_", "tests_", "spec_")
TEST_SUFFIXES = ("_test", "_tests", "_spec", ".test", ".spec")
"""Motifs de nommage reconnus, du ``test_x.py`` de pytest au ``x.spec.ts`` de Jest."""

FRAMEWORK_IMPORTS = {
    "pytest": "pytest",
    "unittest": "unittest",
    "nose": "nose",
    "hypothesis": "hypothesis",
    "doctest": "doctest",
}
"""Import de tête -> cadre de test qu'il révèle."""

FRAMEWORK_PACKAGES = {
    "pytest": "pytest",
    "jest": "Jest",
    "vitest": "Vitest",
    "mocha": "Mocha",
    "jasmine": "Jasmine",
    "ava": "AVA",
    "karma": "Karma",
    "cypress": "Cypress",
    "playwright": "Playwright",
    "@playwright/test": "Playwright",
    "testing-library": "Testing Library",
    "junit": "JUnit",
    "rspec": "RSpec",
    "minitest": "Minitest",
}
"""Dépendance déclarée -> cadre de test qu'elle révèle."""


def is_test_path(relative: str) -> bool:
    """Dit si un chemin désigne un fichier de test.

    Deux indices suffisent, et ils sont indépendants du langage : le fichier
    pend d'un répertoire de tests, ou son nom en porte la marque.

    Args:
        relative: chemin relatif à la racine du dépôt, en séparateurs POSIX.
    """
    path = PurePosixPath(relative)

    if any(part.lower() in TEST_DIRECTORIES for part in path.parts[:-1]):
        return True

    stem = path.name.lower()
    # Le nom est dépouillé de son extension : « app.test.ts » se juge sur « app.test ».
    without_suffix = stem[: -len(path.suffix)] if path.suffix else stem
    return without_suffix.startswith(TEST_PREFIXES) or without_suffix.endswith(TEST_SUFFIXES)


def find_test_directories(paths: Iterable[str]) -> tuple[str, ...]:
    """Relève les répertoires de tests présents dans l'arborescence.

    Le nom évite volontairement le préfixe ``test_`` : pytest collecterait
    sinon cette fonction comme un test dès qu'un module de test l'importe.
    """
    found: set[str] = set()

    for relative in paths:
        parts = PurePosixPath(relative).parts[:-1]
        for index, part in enumerate(parts):
            if part.lower() in TEST_DIRECTORIES:
                found.add("/".join(parts[: index + 1]))

    return tuple(sorted(found))


def count_test_functions(modules: Iterable[ModuleAnalysis]) -> int:
    """Compte les fonctions de test des modules Python analysés.

    Une fonction de test est une fonction dont le nom commence par ``test``,
    convention que partagent pytest et unittest. Les méthodes de classes de test
    sont comptées elles aussi : ``TestChose.test_machin`` en est une.

    Le décompte ne vaut que pour Python, seul langage dont Githor lit l'AST.
    """
    total = 0

    for module in modules:
        if not module.is_test:
            continue
        for function in module.functions:
            # Le nom est qualifié : « Classe.méthode ». C'est le dernier
            # segment qui porte la convention.
            if function.name.rsplit(".", 1)[-1].startswith("test"):
                total += 1

    return total


def detect_frameworks(
    modules: Iterable[ModuleAnalysis], dependencies: Iterable[Dependency]
) -> tuple[str, ...]:
    """Reconnaît les cadres de test employés, par import puis par dépendance.

    Les deux sources se complètent : l'import prouve un usage effectif, la
    dépendance déclarée couvre les cadres qui ne s'importent pas — Jest et
    Vitest s'invoquent par leur runner.
    """
    found: set[str] = set()

    for module in modules:
        for item in module.imports:
            label = FRAMEWORK_IMPORTS.get(item.root)
            if label is not None:
                found.add(label)

    for dependency in dependencies:
        lowered = dependency.name.lower()
        for marker, label in FRAMEWORK_PACKAGES.items():
            if marker in lowered:
                found.add(label)

    return tuple(sorted(found))


def build_test_suite(
    modules: Iterable[ModuleAnalysis], dependencies: Iterable[Dependency]
) -> TestSuite:
    """Rassemble tout ce qu'on sait des tests d'un dépôt.

    Args:
        modules: modules analysés, dont ceux marqués comme fichiers de test.
        dependencies: dépendances déclarées, où se lisent les cadres de test.
    """
    collected = list(modules)
    tests = [module for module in collected if module.is_test]

    return TestSuite(
        files=len(tests),
        functions=count_test_functions(tests),
        frameworks=detect_frameworks(collected, dependencies),
        directories=find_test_directories(module.path for module in collected),
    )
