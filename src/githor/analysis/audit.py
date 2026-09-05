"""Orchestration de l'analyse locale d'un dépôt.

Ce module ne sait rien de GitHub, rien de SQLite et rien de la CLI : il reçoit
un répertoire, le lit, et rend un :class:`CodeAudit`. C'est ce qui permettra à
la V0.3 d'en tirer des scores et à la V0.4 de le commenter, sans que rien de
tout cela ne remonte ici.
"""

from collections import defaultdict
from datetime import datetime
from pathlib import Path

from githor.analysis.dependencies import collect_dependencies
from githor.analysis.languages import language_of
from githor.analysis.loc import count_lines
from githor.analysis.python_ast import (
    SyntaxErrorInModule,
    collect_classes,
    collect_functions,
    collect_imports,
    has_module_docstring,
    parse_module,
)
from githor.analysis.tests import build_test_suite, is_test_path
from githor.analysis.tree import SourceFile, local_roots, read_text, walk_files
from githor.logging import get_logger
from githor.models.code import CodeAudit, LanguageLines, LineCounts, ModuleAnalysis
from githor.utils.dates import utc_now

logger = get_logger("analysis.audit")

DEFAULT_MAX_FILE_BYTES = 1_000_000


def audit_checkout(
    root: Path,
    *,
    commit: str,
    branch: str,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    analysed_at: datetime | None = None,
) -> CodeAudit:
    """Analyse le code présent dans une copie locale.

    Args:
        root: racine du miroir à lire.
        commit: SHA du commit analysé, qui date la mesure.
        branch: branche dont provient ce commit.
        max_file_bytes: taille au-delà de laquelle un fichier est compté sans
            être lu.
        analysed_at: instant de la mesure ; l'instant courant par défaut,
            injectable pour rendre l'analyse reproductible en test.

    Returns:
        L'analyse complète, y compris ce qui n'a pas pu être analysé.
    """
    roots = local_roots(root)

    modules: list[ModuleAnalysis] = []
    seen = binary = too_large = 0

    for source in walk_files(root, max_bytes=max_file_bytes):
        seen += 1
        if source.too_large:
            too_large += 1
            continue
        if source.binary:
            binary += 1
            continue
        modules.append(_analyse_file(source, local_roots=roots))

    dependencies = collect_dependencies(root)

    return CodeAudit(
        analysed_at=analysed_at or utc_now(),
        commit=commit,
        branch=branch,
        files_seen=seen,
        files_analysed=len(modules),
        files_binary=binary,
        files_too_large=too_large,
        modules=tuple(modules),
        languages=_by_language(modules),
        dependencies=dependencies,
        tests=build_test_suite(modules, dependencies),
    )


def _analyse_file(source: SourceFile, *, local_roots: frozenset[str]) -> ModuleAnalysis:
    """Analyse un fichier : ses lignes toujours, sa structure si on sait la lire."""
    language = language_of(source.path)
    content = read_text(source.path)
    lines = count_lines(content, language)
    is_test = is_test_path(source.relative)

    if not language.analysable:
        return ModuleAnalysis(
            path=source.relative,
            language=language.name,
            size_bytes=source.size,
            lines=lines,
            is_test=is_test,
        )

    try:
        tree = parse_module(content, path=source.relative)
    except SyntaxErrorInModule as exc:
        # Un fichier qui ne se parse pas est signalé, jamais passé sous silence :
        # il manquerait sinon à tous les décomptes sans qu'on sache pourquoi.
        logger.debug("Module Python non analysable : %s (%s)", source.relative, exc)
        return ModuleAnalysis(
            path=source.relative,
            language=language.name,
            size_bytes=source.size,
            lines=lines,
            is_test=is_test,
            parse_error=str(exc),
        )

    return ModuleAnalysis(
        path=source.relative,
        language=language.name,
        size_bytes=source.size,
        lines=lines,
        functions=collect_functions(tree),
        classes=collect_classes(tree),
        imports=collect_imports(tree, local_roots=local_roots),
        has_docstring=has_module_docstring(tree),
        is_test=is_test,
    )


def _by_language(modules: list[ModuleAnalysis]) -> tuple[LanguageLines, ...]:
    """Agrège les modules par langage, du plus volumineux au moins volumineux.

    Le tri se fait sur les lignes de code, non sur le nombre de fichiers : cent
    fichiers de configuration ne font pas d'un dépôt un projet YAML.
    """
    files: dict[str, int] = defaultdict(int)
    lines: dict[str, LineCounts] = defaultdict(LineCounts)

    for module in modules:
        files[module.language] += 1
        lines[module.language] = lines[module.language] + module.lines

    ranked = sorted(files, key=lambda name: (-lines[name].code, name))
    return tuple(
        LanguageLines(language=name, files=files[name], lines=lines[name]) for name in ranked
    )
