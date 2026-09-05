"""Analyse locale du code : lignes, structure, complexité, imports.

Cette couche lit des fichiers sur disque et n'en sait pas davantage. Elle
ignore GitHub, la base et la CLI, exactement comme les ``rules`` de la V0.1 :
c'est ce qui permet d'y brancher une nouvelle source sans rien réécrire.

Ce qu'elle sait faire dépend du langage, et elle le dit plutôt que de le
deviner. Les lignes sont comptées pour tout ce qu'elle reconnaît ; la structure,
la complexité et les imports ne sont établis que pour Python, où ils viennent de
l'AST de l'interpréteur et non d'une heuristique.
"""

from githor.analysis.audit import audit_checkout
from githor.analysis.dependencies import collect_dependencies, parse_requirement
from githor.analysis.languages import LANGUAGES, Language, is_analysable, language_of
from githor.analysis.loc import count_lines
from githor.analysis.python_ast import (
    STDLIB_MODULES,
    SyntaxErrorInModule,
    classify_import,
    collect_classes,
    collect_functions,
    collect_imports,
    complexity_of,
    parse_module,
)
from githor.analysis.tests import build_test_suite, find_test_directories, is_test_path
from githor.analysis.tree import EXCLUDED_DIRECTORIES, SourceFile, local_roots, walk_files

__all__ = [
    "EXCLUDED_DIRECTORIES",
    "LANGUAGES",
    "STDLIB_MODULES",
    "Language",
    "SourceFile",
    "SyntaxErrorInModule",
    "audit_checkout",
    "build_test_suite",
    "classify_import",
    "collect_classes",
    "collect_functions",
    "collect_dependencies",
    "collect_imports",
    "complexity_of",
    "count_lines",
    "find_test_directories",
    "is_analysable",
    "is_test_path",
    "language_of",
    "local_roots",
    "parse_module",
    "parse_requirement",
    "walk_files",
]
