"""Tests de l'analyse locale : langages, lignes, AST, complexité, imports.

Rien ici ne joint le réseau ni ne dépend d'un dépôt réel : les fichiers analysés
sont écrits dans un répertoire temporaire, ce qui rend chaque attente vérifiable
à la main.
"""

import ast
from pathlib import Path

import pytest

from githor.analysis.audit import audit_checkout
from githor.analysis.languages import UNKNOWN, language_of
from githor.analysis.loc import count_lines
from githor.analysis.python_ast import (
    SyntaxErrorInModule,
    classify_import,
    collect_classes,
    collect_functions,
    collect_imports,
    complexity_of,
    parse_module,
)
from githor.analysis.tree import local_roots, looks_binary, walk_files
from githor.models.code import ImportKind, LineCounts

PYTHON = language_of(Path("module.py"))
TYPESCRIPT = language_of(Path("module.ts"))


def parsed(source: str) -> ast.Module:
    """Parse un module de test."""
    return parse_module(source, path="test.py")


# ── Reconnaissance des langages ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("module.py", "Python"),
        ("Module.PY", "Python"),
        ("app.tsx", "TypeScript"),
        ("style.scss", "SCSS"),
        ("Dockerfile", "Dockerfile"),
        ("Makefile", "Makefile"),
        ("Dockerfile.dev", "Dockerfile"),
        ("config.toml", "TOML"),
        ("README.md", "Markdown"),
    ],
)
def test_a_file_is_recognised_by_name_or_extension(name: str, expected: str) -> None:
    assert language_of(Path(name)).name == expected


def test_an_unknown_extension_is_named_as_such() -> None:
    assert language_of(Path("chose.zzz")) is UNKNOWN


def test_only_python_is_analysed_beyond_its_lines() -> None:
    """Compter les fonctions d'un TypeScript à l'expression régulière mentirait."""
    assert PYTHON.analysable is True
    assert TYPESCRIPT.analysable is False


# ── Décompte des lignes ──────────────────────────────────────────────────────


def test_the_three_categories_always_sum_to_the_total() -> None:
    counts = count_lines("a = 1\n\n# note\nb = 2\n", PYTHON)

    assert counts == LineCounts(total=4, code=2, comment=1, blank=1)


def test_a_line_mixing_code_and_comment_counts_as_code() -> None:
    """C'est le code qui s'exécute ; le commentaire ne fait que l'accompagner."""
    counts = count_lines("a = 1  # note\n", PYTHON)

    assert counts.code == 1
    assert counts.comment == 0


def test_a_whitespace_only_line_is_blank() -> None:
    assert count_lines("   \n\t\n", PYTHON).blank == 2


def test_a_python_docstring_is_code_not_comment() -> None:
    """Une docstring est évaluée et lisible à l'exécution : c'est du code."""
    counts = count_lines('"""Titre."""\na = 1\n', PYTHON)

    assert counts.comment == 0
    assert counts.code == 2


def test_a_block_comment_spans_several_lines() -> None:
    counts = count_lines("/* un\n   commentaire */\nconst a = 1;\n", TYPESCRIPT)

    assert counts == LineCounts(total=3, code=1, comment=2, blank=0)


def test_a_block_opened_and_closed_on_one_line_is_a_comment() -> None:
    assert count_lines("/* court */\n", TYPESCRIPT).comment == 1


def test_code_following_a_closed_block_counts_as_code() -> None:
    assert count_lines("/* note */ const a = 1;\n", TYPESCRIPT).code == 1


def test_an_unterminated_block_does_not_swallow_the_count() -> None:
    counts = count_lines("/* jamais fermé\nligne\nligne\n", TYPESCRIPT)

    assert counts.total == 3
    assert counts.comment == 3


def test_an_empty_file_counts_nothing() -> None:
    assert count_lines("", PYTHON) == LineCounts()


def test_the_comment_ratio_excludes_blank_lines() -> None:
    """Commenter n'a de sens que rapporté à ce qui est écrit."""
    counts = LineCounts(total=4, code=3, comment=1, blank=0)

    assert counts.comment_ratio == 25.0


def test_a_file_without_content_has_no_comment_ratio() -> None:
    assert LineCounts(total=2, blank=2).comment_ratio is None


# ── Complexité cyclomatique ──────────────────────────────────────────────────


def complexity(source: str) -> int:
    """Complexité de la première fonction d'un module de test."""
    return collect_functions(parsed(source))[0].complexity


def test_a_straight_function_has_a_complexity_of_one() -> None:
    assert complexity("def f():\n    return 1\n") == 1


def test_each_branch_adds_one() -> None:
    assert complexity("def f(x):\n    if x:\n        return 1\n    return 0\n") == 2


def test_a_loop_adds_one() -> None:
    assert complexity("def f(xs):\n    for x in xs:\n        pass\n") == 2


def test_an_except_handler_adds_one() -> None:
    source = "def f():\n    try:\n        pass\n    except ValueError:\n        pass\n"
    assert complexity(source) == 2


def test_each_extra_boolean_term_adds_one() -> None:
    """« a and b and c » court-circuite deux fois : ce sont deux chemins de plus."""
    assert complexity("def f(a, b, c):\n    return a and b and c\n") == 3


def test_a_comprehension_condition_adds_one() -> None:
    assert complexity("def f(xs):\n    return [x for x in xs if x]\n") == 3


def test_a_match_case_adds_one_per_case() -> None:
    source = (
        "def f(x):\n"
        "    match x:\n"
        "        case 1:\n"
        "            pass\n"
        "        case _:\n"
        "            pass\n"
    )

    assert complexity(source) == 3


def test_a_nested_function_is_not_counted_twice() -> None:
    """La complexité d'une fonction interne lui appartient : l'ajouter la compterait deux fois."""
    source = (
        "def externe(x):\n"
        "    def interne(y):\n"
        "        if y:\n"
        "            return 1\n"
        "        return 0\n"
        "    return interne\n"
    )
    functions = {item.name: item.complexity for item in collect_functions(parsed(source))}

    assert functions["externe"] == 1
    assert functions["externe.interne"] == 2


def test_complexity_of_a_module_covers_its_top_level() -> None:
    assert complexity_of(parsed("if True:\n    pass\n")) == 2


# ── Structure ────────────────────────────────────────────────────────────────


def test_a_method_is_named_after_its_class() -> None:
    """Un rapport qui citerait « check » sans sa classe ne servirait à rien."""
    source = "class Règle:\n    def check(self):\n        pass\n"

    names = [item.name for item in collect_functions(parsed(source))]

    assert names == ["Règle.check"]


def test_arguments_include_variadics() -> None:
    source = "def f(a, /, b, *args, c, **kwargs):\n    pass\n"

    assert collect_functions(parsed(source))[0].arguments == 5


def test_an_async_function_is_marked_as_such() -> None:
    assert collect_functions(parsed("async def f():\n    pass\n"))[0].is_async is True


def test_a_docstring_is_noticed() -> None:
    source = 'def f():\n    """Fait quelque chose."""\n'

    assert collect_functions(parsed(source))[0].has_docstring is True


def test_a_class_reports_its_direct_methods() -> None:
    source = 'class A:\n    """Doc."""\n    def un(self): pass\n    def deux(self): pass\n'

    classes = collect_classes(parsed(source))

    assert classes[0].name == "A"
    assert classes[0].methods == 2
    assert classes[0].has_docstring is True


def test_a_nested_class_is_named_after_its_parent() -> None:
    source = "class A:\n    class B:\n        pass\n"

    assert [item.name for item in collect_classes(parsed(source))] == ["A", "A.B"]


def test_invalid_python_is_reported_with_its_line() -> None:
    with pytest.raises(SyntaxErrorInModule, match="ligne 1"):
        parse_module("def (:\n", path="cassé.py")


# ── Imports ──────────────────────────────────────────────────────────────────


def test_a_standard_library_import_is_recognised() -> None:
    assert classify_import("pathlib", level=0, local_roots=frozenset()) is ImportKind.STDLIB


def test_a_repository_package_is_local() -> None:
    """C'est ce qui distingue « import githor.config » d'une dépendance."""
    kind = classify_import("githor.config", level=0, local_roots=frozenset({"githor"}))

    assert kind is ImportKind.LOCAL


def test_a_relative_import_is_local_by_construction() -> None:
    assert classify_import("outils", level=1, local_roots=frozenset()) is ImportKind.LOCAL


def test_anything_else_must_be_installed() -> None:
    assert classify_import("httpx", level=0, local_roots=frozenset()) is ImportKind.THIRD_PARTY


def test_imports_are_collected_with_their_line() -> None:
    source = "import os\nfrom httpx import Client\n"

    imports = collect_imports(parsed(source), local_roots=frozenset())

    assert [(item.module, item.kind, item.line) for item in imports] == [
        ("os", ImportKind.STDLIB, 1),
        ("httpx", ImportKind.THIRD_PARTY, 2),
    ]


def test_an_import_inside_a_function_is_still_an_import() -> None:
    """Un import fait pour casser un cycle conditionne l'exécution comme les autres."""
    source = "def f():\n    import httpx\n    return httpx\n"

    imports = collect_imports(parsed(source), local_roots=frozenset())

    assert [item.module for item in imports] == ["httpx"]


def test_the_root_of_an_import_is_what_gets_installed() -> None:
    imports = collect_imports(parsed("import a.b.c\n"), local_roots=frozenset())

    assert imports[0].root == "a"


# ── Parcours de l'arborescence ───────────────────────────────────────────────


def build(root: Path, files: dict[str, str]) -> None:
    """Écrit une arborescence de test."""
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def test_artefact_directories_are_skipped(tmp_path: Path) -> None:
    build(
        tmp_path,
        {
            "app.py": "a = 1\n",
            "node_modules/paquet/index.js": "x\n",
            ".venv/lib/module.py": "y\n",
            "src/__pycache__/app.cpython-313.pyc": "z\n",
        },
    )

    seen = {item.relative for item in walk_files(tmp_path, max_bytes=10_000)}

    assert seen == {"app.py"}


def test_a_file_too_large_is_counted_but_not_read(tmp_path: Path) -> None:
    build(tmp_path, {"gros.py": "a = 1\n" * 500})

    found = list(walk_files(tmp_path, max_bytes=100))

    assert found[0].too_large is True
    assert found[0].readable is False


def test_a_binary_file_is_recognised(tmp_path: Path) -> None:
    (tmp_path / "image.png").write_bytes(b"\x89PNG\x00\x00binaire")

    found = list(walk_files(tmp_path, max_bytes=10_000))

    assert found[0].binary is True


def test_a_symlink_is_never_followed(tmp_path: Path) -> None:
    """Un lien permettrait à un dépôt de faire sortir l'analyse de son miroir."""
    build(tmp_path, {"réel.py": "a = 1\n"})
    (tmp_path / "lien.py").symlink_to(tmp_path / "réel.py")

    seen = {item.relative for item in walk_files(tmp_path, max_bytes=10_000)}

    assert seen == {"réel.py"}


def test_a_text_file_is_not_binary(tmp_path: Path) -> None:
    build(tmp_path, {"texte.py": "a = 1\n"})

    assert looks_binary(tmp_path / "texte.py") is False


def test_local_roots_cover_the_src_layout(tmp_path: Path) -> None:
    """Sans cela, tout projet moderne verrait ses propres modules comptés comme tierce partie."""
    build(
        tmp_path,
        {
            "src/monpaquet/__init__.py": "",
            "tests/test_un.py": "",
            "outil.py": "",
        },
    )

    assert local_roots(tmp_path) == frozenset({"monpaquet", "tests", "outil"})


# ── Analyse complète ─────────────────────────────────────────────────────────


def test_an_audit_describes_the_whole_repository(tmp_path: Path) -> None:
    build(
        tmp_path,
        {
            "src/paquet/__init__.py": '"""Paquet."""\n',
            "src/paquet/app.py": (
                '"""Module."""\n'
                "import os\n"
                "import httpx\n"
                "from paquet import outils\n"
                "\n"
                "class Service:\n"
                "    def traiter(self, x):\n"
                "        if x:\n"
                "            return 1\n"
                "        return 0\n"
            ),
            "README.md": "# Titre\n",
        },
    )

    result = audit_checkout(tmp_path, commit="abc1234", branch="main")

    assert result.files_seen == 3
    assert result.files_analysed == 3
    assert result.class_count == 1
    assert [item.name for item in result.functions] == ["Service.traiter"]
    assert result.max_complexity == 2
    assert result.imports_of_kind(ImportKind.THIRD_PARTY) == ("httpx",)
    assert result.imports_of_kind(ImportKind.LOCAL) == ("paquet",)
    assert result.imports_of_kind(ImportKind.STDLIB) == ("os",)


def test_languages_are_ranked_by_code_not_by_file_count(tmp_path: Path) -> None:
    """Cent fichiers de configuration ne font pas d'un dépôt un projet YAML."""
    build(tmp_path, {"app.py": "a = 1\n" * 50})
    for index in range(10):
        build(tmp_path, {f"conf{index}.yml": "clé: valeur\n"})

    result = audit_checkout(tmp_path, commit="abc", branch="main")

    assert result.languages[0].language == "Python"
    assert result.languages[1].language == "YAML"
    assert result.languages[1].files == 10


def test_an_unparsable_module_is_reported_not_hidden(tmp_path: Path) -> None:
    build(tmp_path, {"bon.py": "a = 1\n", "cassé.py": "def (:\n"})

    result = audit_checkout(tmp_path, commit="abc", branch="main")

    assert result.files_analysed == 2
    assert [item.path for item in result.parse_errors] == ["cassé.py"]
    # Ses lignes restent comptées : le fichier existe, quoi qu'il contienne.
    assert result.lines.total == 2


def test_an_empty_repository_has_no_average_complexity(tmp_path: Path) -> None:
    """Un dépôt sans fonction n'a pas une complexité de zéro : il n'en a pas."""
    build(tmp_path, {"README.md": "# Titre\n"})

    result = audit_checkout(tmp_path, commit="abc", branch="main")

    assert result.average_complexity is None
    assert result.max_complexity == 0


def test_the_audit_names_the_analysed_commit(tmp_path: Path) -> None:
    build(tmp_path, {"app.py": "a = 1\n"})

    result = audit_checkout(tmp_path, commit="0123456789abcdef", branch="develop")

    assert result.commit == "0123456789abcdef"
    assert result.branch == "develop"
    assert result.analysed_at.tzinfo is not None


def test_the_most_complex_functions_come_first(tmp_path: Path) -> None:
    build(
        tmp_path,
        {
            "app.py": (
                "def simple():\n"
                "    return 1\n"
                "def compliquée(a, b):\n"
                "    if a:\n"
                "        if b:\n"
                "            return 1\n"
                "    return 0\n"
            )
        },
    )

    result = audit_checkout(tmp_path, commit="abc", branch="main")

    assert [item.name for item in result.most_complex(1)] == ["compliquée"]


def test_a_file_beyond_the_limit_is_counted_apart(tmp_path: Path) -> None:
    build(tmp_path, {"petit.py": "a = 1\n", "gros.py": "a = 1\n" * 500})

    result = audit_checkout(tmp_path, commit="abc", branch="main", max_file_bytes=100)

    assert result.files_seen == 2
    assert result.files_analysed == 1
    assert result.files_too_large == 1
