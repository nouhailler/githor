"""Tests des dépendances déclarées et de la détection des tests (étape 16).

Les manifestes sont écrits dans un répertoire temporaire : chaque attente se
vérifie à la main, et rien n'est installé ni exécuté.
"""

from pathlib import Path

import pytest

from githor.analysis.dependencies import collect_dependencies, parse_requirement
from githor.analysis.tests import (
    build_test_suite,
    count_test_functions,
    detect_frameworks,
    find_test_directories,
    is_test_path,
)
from githor.models.code import (
    Dependency,
    DependencyScope,
    FunctionAnalysis,
    Import,
    ImportKind,
    ModuleAnalysis,
)


def write(root: Path, name: str, content: str) -> Path:
    """Écrit un manifeste de test."""
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def names(root: Path, scope: DependencyScope | None = None) -> list[str]:
    """Noms des dépendances relevées, éventuellement filtrées par rôle."""
    found = collect_dependencies(root)
    if scope is not None:
        found = tuple(item for item in found if item.scope is scope)
    return [item.name for item in found]


# ── Exigences PEP 508 ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("line", "expected_name", "expected_specifier"),
    [
        ("httpx", "httpx", None),
        ("httpx>=0.27", "httpx", ">=0.27"),
        ("httpx[cli]>=0.27", "httpx", ">=0.27"),
        ("ruamel.yaml==0.18", "ruamel.yaml", "==0.18"),
        ("pytest >= 8.0", "pytest", ">= 8.0"),
    ],
)
def test_a_requirement_is_split_into_name_and_constraint(
    line: str, expected_name: str, expected_specifier: str | None
) -> None:
    found = parse_requirement(line, source="requirements.txt", scope=DependencyScope.RUNTIME)

    assert found is not None
    assert found.name == expected_name
    assert found.specifier == expected_specifier


def test_an_environment_marker_belongs_neither_to_the_name_nor_the_constraint() -> None:
    found = parse_requirement(
        'tomli>=2.0; python_version < "3.11"',
        source="requirements.txt",
        scope=DependencyScope.RUNTIME,
    )

    assert found is not None
    assert found.specifier == ">=2.0"


@pytest.mark.parametrize("line", ["", "   ", "# un commentaire", ";"])
def test_a_line_without_a_requirement_yields_nothing(line: str) -> None:
    assert parse_requirement(line, source="r.txt", scope=DependencyScope.RUNTIME) is None


# ── pyproject.toml ───────────────────────────────────────────────────────────


def test_pep_621_dependencies_are_read(tmp_path: Path) -> None:
    write(
        tmp_path,
        "pyproject.toml",
        '[project]\nname = "x"\ndependencies = ["httpx>=0.27", "rich"]\n',
    )

    assert names(tmp_path) == ["httpx", "rich"]


def test_optional_dependencies_keep_their_extra(tmp_path: Path) -> None:
    write(
        tmp_path,
        "pyproject.toml",
        '[project]\nname = "x"\n[project.optional-dependencies]\ndev = ["pytest>=8"]\n',
    )

    found = collect_dependencies(tmp_path)

    assert found[0].scope is DependencyScope.OPTIONAL
    assert found[0].group == "dev"


def test_dependency_groups_are_development_only(tmp_path: Path) -> None:
    """PEP 735 : ces groupes ne s'installent jamais avec le paquet."""
    write(tmp_path, "pyproject.toml", '[dependency-groups]\ntest = ["pytest"]\n')

    assert names(tmp_path, DependencyScope.DEVELOPMENT) == ["pytest"]


def test_poetry_tables_are_read(tmp_path: Path) -> None:
    write(
        tmp_path,
        "pyproject.toml",
        '[tool.poetry.dependencies]\npython = "^3.13"\nhttpx = "^0.27"\n'
        '[tool.poetry.group.dev.dependencies]\npytest = "^8.0"\n',
    )

    found = {item.name: item for item in collect_dependencies(tmp_path)}

    # « python » désigne l'interpréteur, pas un paquet installable.
    assert "python" not in found
    assert found["httpx"].specifier == "^0.27"
    assert found["pytest"].scope is DependencyScope.DEVELOPMENT


def test_a_broken_manifest_does_not_break_the_analysis(tmp_path: Path) -> None:
    """Un pyproject cassé est un problème du dépôt analysé, pas de Githor."""
    write(tmp_path, "pyproject.toml", "ceci [n'est pas = du TOML\n")

    assert collect_dependencies(tmp_path) == ()


def test_a_missing_manifest_is_not_an_error(tmp_path: Path) -> None:
    assert collect_dependencies(tmp_path) == ()


# ── Autres écosystèmes ───────────────────────────────────────────────────────


def test_requirements_files_are_read(tmp_path: Path) -> None:
    write(tmp_path, "requirements.txt", "httpx>=0.27\n# note\nrich\n")

    assert names(tmp_path) == ["httpx", "rich"]


def test_requirements_directives_are_not_dependencies(tmp_path: Path) -> None:
    write(tmp_path, "requirements.txt", "-r base.txt\n-e .\n--index-url https://x\nhttpx\n")

    assert names(tmp_path) == ["httpx"]


def test_a_dev_requirements_file_declares_tooling(tmp_path: Path) -> None:
    write(tmp_path, "requirements-dev.txt", "pytest\n")

    assert names(tmp_path, DependencyScope.DEVELOPMENT) == ["pytest"]


def test_setup_cfg_install_requires_is_read(tmp_path: Path) -> None:
    write(tmp_path, "setup.cfg", "[options]\ninstall_requires =\n    httpx>=0.27\n    rich\n")

    assert names(tmp_path) == ["httpx", "rich"]


def test_package_json_separates_runtime_from_development(tmp_path: Path) -> None:
    write(
        tmp_path,
        "package.json",
        '{"dependencies": {"react": "^18"}, "devDependencies": {"vitest": "^1"}}',
    )

    found = {item.name: item for item in collect_dependencies(tmp_path)}

    assert found["react"].scope is DependencyScope.RUNTIME
    assert found["react"].ecosystem == "npm"
    assert found["vitest"].scope is DependencyScope.DEVELOPMENT


def test_cargo_dependencies_are_read(tmp_path: Path) -> None:
    write(
        tmp_path,
        "Cargo.toml",
        '[dependencies]\nserde = "1.0"\ntokio = { version = "1", features = ["full"] }\n',
    )

    found = {item.name: item for item in collect_dependencies(tmp_path)}

    assert found["serde"].specifier == "1.0"
    assert found["tokio"].specifier == "1"
    assert found["serde"].ecosystem == "crates.io"


def test_go_modules_are_read_inside_and_outside_blocks(tmp_path: Path) -> None:
    write(
        tmp_path,
        "go.mod",
        "module exemple\n\ngo 1.22\n\nrequire github.com/a/b v1.2.3\n\n"
        "require (\n\tgithub.com/c/d v0.1.0 // indirect\n)\n",
    )

    assert names(tmp_path) == ["github.com/a/b", "github.com/c/d"]


def test_each_dependency_names_the_manifest_that_declared_it(tmp_path: Path) -> None:
    """Un constat doit toujours pouvoir citer ce qui le motive."""
    write(tmp_path, "requirements/base.txt", "httpx\n")

    assert collect_dependencies(tmp_path)[0].source == "requirements/base.txt"


def test_the_same_package_declared_twice_is_kept_once_per_manifest(tmp_path: Path) -> None:
    write(tmp_path, "pyproject.toml", '[project]\nname = "x"\ndependencies = ["httpx"]\n')
    write(tmp_path, "requirements.txt", "httpx\n")

    sources = sorted(item.source for item in collect_dependencies(tmp_path))

    assert sources == ["pyproject.toml", "requirements.txt"]


# ── Détection des tests ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_un.py",
        "test/module.py",
        "src/__tests__/app.js",
        "test_module.py",
        "module_test.go",
        "app.test.ts",
        "app.spec.js",
        "spec/exemple_spec.rb",
    ],
)
def test_a_test_file_is_recognised_across_languages(path: str) -> None:
    assert is_test_path(path) is True


@pytest.mark.parametrize(
    "path",
    ["src/app.py", "latest.py", "contest.js", "src/protest/module.py"],
)
def test_a_source_file_is_not_mistaken_for_a_test(path: str) -> None:
    """« latest » et « contest » contiennent « test » sans être des tests."""
    assert is_test_path(path) is False


def test_test_directories_are_reported(tmp_path: Path) -> None:
    found = find_test_directories(["tests/test_a.py", "src/app.py", "src/__tests__/b.js"])

    assert found == ("src/__tests__", "tests")


def module(
    path: str, *, functions: tuple[str, ...] = (), imports: tuple[str, ...] = ()
) -> ModuleAnalysis:
    """Construit un module analysé pour les tests."""
    return ModuleAnalysis(
        path=path,
        language="Python",
        is_test=is_test_path(path),
        functions=tuple(FunctionAnalysis(name=name, line=1) for name in functions),
        imports=tuple(Import(module=name, kind=ImportKind.THIRD_PARTY, line=1) for name in imports),
    )


def test_test_functions_are_counted_in_test_files_only() -> None:
    modules = [
        module("tests/test_a.py", functions=("test_un", "test_deux", "aide")),
        module("src/app.py", functions=("test_ne_compte_pas",)),
    ]

    assert count_test_functions(modules) == 2


def test_a_test_method_of_a_test_class_is_counted() -> None:
    """C'est le dernier segment du nom qualifié qui porte la convention."""
    modules = [module("tests/test_a.py", functions=("TestChose.test_machin", "TestChose.aide"))]

    assert count_test_functions(modules) == 1


def test_a_framework_is_recognised_by_its_import() -> None:
    modules = [module("tests/test_a.py", imports=("pytest",))]

    assert detect_frameworks(modules, []) == ("pytest",)


def test_a_framework_is_recognised_by_its_declared_dependency() -> None:
    """Jest et Vitest s'invoquent par leur runner : ils ne s'importent pas."""
    dependency = Dependency(name="vitest", ecosystem="npm", source="package.json")

    assert detect_frameworks([], [dependency]) == ("Vitest",)


def test_a_repository_without_tests_says_so() -> None:
    suite = build_test_suite([module("src/app.py")], [])

    assert suite.exists is False
    assert suite.files == 0


def test_the_suite_gathers_everything_known_about_the_tests() -> None:
    modules = [
        module("src/app.py"),
        module("tests/test_a.py", functions=("test_un",), imports=("pytest",)),
    ]

    suite = build_test_suite(modules, [])

    assert suite.files == 1
    assert suite.functions == 1
    assert suite.frameworks == ("pytest",)
    assert suite.directories == ("tests",)
