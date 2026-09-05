"""Écriture et lecture des audits de code.

Un audit s'**ajoute**, comme un snapshot : deux analyses successives se
comparent, elles ne se remplacent pas. C'est ce qui permettra à la V0.3 de
répondre à « ce projet s'est-il complexifié depuis six mois ? ».

Rien d'agrégé n'est écrit. Lignes, langages, décomptes de fonctions et
statistiques de complexité se recalculent ici, à la lecture, depuis les faits
enregistrés : stocker un chiffre à côté de sa source, c'est accepter qu'ils
divergent un jour.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from githor.analysis.tests import (
    FRAMEWORK_IMPORTS,
    FRAMEWORK_PACKAGES,
    find_test_directories,
)
from githor.logging import get_logger
from githor.models.code import (
    CodeAudit,
    Dependency,
    DependencyScope,
    LanguageLines,
    LineCounts,
    TestSuite,
)
from githor.storage.tables import (
    CodeAuditRow,
    CodeClassRow,
    CodeDependencyRow,
    CodeFunctionRow,
    CodeImportRow,
    CodeModuleRow,
)

logger = get_logger("storage.code")


def save_audit(session: Session, repository_id: int, audit: CodeAudit) -> CodeAuditRow:
    """Enregistre un audit et tout ce qu'il a relevé.

    Args:
        session: session ouverte.
        repository_id: identifiant interne du repository analysé.
        audit: résultat de l'analyse locale.

    Returns:
        La ligne d'audit créée.
    """
    row = CodeAuditRow(
        repository_id=repository_id,
        analysed_at=audit.analysed_at,
        commit=audit.commit,
        branch=audit.branch,
        files_seen=audit.files_seen,
        files_binary=audit.files_binary,
        files_too_large=audit.files_too_large,
    )
    session.add(row)
    session.flush()

    for module in audit.modules:
        module_row = CodeModuleRow(
            audit_id=row.id,
            path=module.path,
            language=module.language,
            size_bytes=module.size_bytes,
            lines_total=module.lines.total,
            lines_code=module.lines.code,
            lines_comment=module.lines.comment,
            lines_blank=module.lines.blank,
            is_test=module.is_test,
            has_docstring=module.has_docstring,
            parse_error=module.parse_error,
        )
        session.add(module_row)
        session.flush()

        session.add_all(
            CodeFunctionRow(
                module_id=module_row.id,
                name=function.name,
                line=function.line,
                complexity=function.complexity,
                arguments=function.arguments,
                is_async=function.is_async,
                has_docstring=function.has_docstring,
            )
            for function in module.functions
        )
        session.add_all(
            CodeClassRow(
                module_id=module_row.id,
                name=item.name,
                line=item.line,
                methods=item.methods,
                has_docstring=item.has_docstring,
            )
            for item in module.classes
        )
        session.add_all(
            CodeImportRow(
                module_id=module_row.id,
                module_name=item.module,
                root=item.root,
                kind=item.kind,
                line=item.line,
            )
            for item in module.imports
        )

    session.add_all(
        CodeDependencyRow(
            audit_id=row.id,
            name=item.name,
            ecosystem=item.ecosystem,
            scope=item.scope,
            specifier=item.specifier,
            source=item.source,
            group=item.group,
        )
        for item in audit.dependencies
    )

    session.flush()
    logger.debug(
        "Audit enregistré : repository %s, commit %s, %s module(s).",
        repository_id,
        audit.commit[:7],
        len(audit.modules),
    )
    return row


def latest_audit(session: Session, repository_id: int) -> CodeAuditRow | None:
    """Retourne l'audit le plus récent d'un repository, ``None`` s'il n'y en a pas."""
    return session.scalar(
        select(CodeAuditRow)
        .where(CodeAuditRow.repository_id == repository_id)
        .order_by(CodeAuditRow.analysed_at.desc(), CodeAuditRow.id.desc())
        .limit(1)
    )


def first_audit(session: Session, repository_id: int) -> CodeAuditRow | None:
    """Retourne le premier audit d'un repository, ``None`` s'il n'y en a pas."""
    return session.scalar(
        select(CodeAuditRow)
        .where(CodeAuditRow.repository_id == repository_id)
        .order_by(CodeAuditRow.analysed_at, CodeAuditRow.id)
        .limit(1)
    )


def count_audits(session: Session, repository_id: int) -> int:
    """Nombre d'audits conservés pour un repository."""
    total = session.scalar(
        select(func.count())
        .select_from(CodeAuditRow)
        .where(CodeAuditRow.repository_id == repository_id)
    )
    return int(total or 0)


def last_analysed_at(session: Session, repository_ids: Sequence[int]) -> dict[int, datetime]:
    """Date du dernier audit de chaque repository, en une seule requête.

    Même geste que pour les snapshots : une requête avant la boucle plutôt
    qu'une par dépôt.

    Args:
        session: session ouverte.
        repository_ids: identifiants internes à interroger.

    Returns:
        Identifiant interne -> date du dernier audit. Les dépôts jamais
        analysés sont simplement absents.
    """
    if not repository_ids:
        return {}

    rows = session.execute(
        select(CodeAuditRow.repository_id, func.max(CodeAuditRow.analysed_at))
        .where(CodeAuditRow.repository_id.in_(repository_ids))
        .group_by(CodeAuditRow.repository_id)
    ).all()

    return {int(repository_id): analysed_at for repository_id, analysed_at in rows if analysed_at}


@dataclass(frozen=True)
class ComplexFunction:
    """Une fonction complexe, avec le fichier qui la contient."""

    name: str
    path: str
    line: int
    complexity: int


@dataclass(frozen=True)
class AuditMetrics:
    """Métriques d'un audit, recalculées depuis les faits enregistrés.

    Aucune de ces valeurs n'est stockée : elles se dérivent des tables, ce qui
    garantit qu'un chiffre ne peut pas diverger de ce dont il est tiré.
    """

    analysed_at: datetime
    commit: str
    branch: str

    files_seen: int = 0
    files_analysed: int = 0
    files_binary: int = 0
    files_too_large: int = 0

    lines: LineCounts = field(default_factory=LineCounts)
    languages: tuple[LanguageLines, ...] = ()

    function_count: int = 0
    class_count: int = 0
    average_complexity: float | None = None
    max_complexity: int = 0
    most_complex: tuple[ComplexFunction, ...] = ()

    parse_errors: int = 0
    dependencies: tuple[Dependency, ...] = ()
    tests: TestSuite = field(default_factory=TestSuite)

    @property
    def short_commit(self) -> str:
        """SHA abrégé, tel que ``git`` l'affiche."""
        return self.commit[:7]

    def dependencies_in_scope(self, scope: DependencyScope) -> tuple[Dependency, ...]:
        """Dépendances déclarées pour un rôle donné."""
        return tuple(item for item in self.dependencies if item.scope is scope)


def audit_metrics(session: Session, audit: CodeAuditRow, *, complex_limit: int = 5) -> AuditMetrics:
    """Recalcule les métriques d'un audit depuis la base.

    Args:
        session: session ouverte.
        audit: audit dont on veut les métriques.
        complex_limit: nombre de fonctions les plus complexes retenues.
    """
    modules = list(session.scalars(select(CodeModuleRow).where(CodeModuleRow.audit_id == audit.id)))
    module_ids = [module.id for module in modules]

    lines = LineCounts(
        total=sum(module.lines_total for module in modules),
        code=sum(module.lines_code for module in modules),
        comment=sum(module.lines_comment for module in modules),
        blank=sum(module.lines_blank for module in modules),
    )

    functions = _function_statistics(session, module_ids)
    class_count = _count_classes(session, module_ids)

    return AuditMetrics(
        analysed_at=audit.analysed_at,
        commit=audit.commit,
        branch=audit.branch,
        files_seen=audit.files_seen,
        files_analysed=len(modules),
        files_binary=audit.files_binary,
        files_too_large=audit.files_too_large,
        lines=lines,
        languages=_languages(modules),
        function_count=functions[0],
        class_count=class_count,
        average_complexity=functions[1],
        max_complexity=functions[2],
        most_complex=_most_complex(session, module_ids, limit=complex_limit),
        parse_errors=sum(1 for module in modules if module.parse_error is not None),
        dependencies=_dependencies(session, audit.id),
        tests=_tests(session, audit.id, modules),
    )


def _count_classes(session: Session, module_ids: Sequence[int]) -> int:
    """Nombre de classes relevées dans les modules donnés."""
    if not module_ids:
        return 0
    total = session.scalar(
        select(func.count()).select_from(CodeClassRow).where(CodeClassRow.module_id.in_(module_ids))
    )
    return int(total or 0)


def _function_statistics(
    session: Session, module_ids: Sequence[int]
) -> tuple[int, float | None, int]:
    """Nombre de fonctions, complexité moyenne et maximale.

    La moyenne est nulle plutôt que zéro quand il n'y a aucune fonction : un
    dépôt sans fonction analysable n'a pas une complexité de zéro, il n'en a pas.
    """
    if not module_ids:
        return 0, None, 0

    row = session.execute(
        select(
            func.count(CodeFunctionRow.id),
            func.avg(CodeFunctionRow.complexity),
            func.max(CodeFunctionRow.complexity),
        ).where(CodeFunctionRow.module_id.in_(module_ids))
    ).one()

    count = int(row[0] or 0)
    if count == 0:
        return 0, None, 0
    return count, round(float(row[1]), 2), int(row[2] or 0)


def _most_complex(
    session: Session, module_ids: Sequence[int], *, limit: int
) -> tuple[ComplexFunction, ...]:
    """Fonctions les plus complexes, avec le fichier qui les contient."""
    if not module_ids or limit <= 0:
        return ()

    rows = session.execute(
        select(
            CodeFunctionRow.name,
            CodeModuleRow.path,
            CodeFunctionRow.line,
            CodeFunctionRow.complexity,
        )
        .join(CodeModuleRow, CodeModuleRow.id == CodeFunctionRow.module_id)
        .where(CodeFunctionRow.module_id.in_(module_ids))
        .order_by(CodeFunctionRow.complexity.desc(), CodeFunctionRow.name)
        .limit(limit)
    ).all()

    return tuple(
        ComplexFunction(name=name, path=path, line=line, complexity=complexity)
        for name, path, line, complexity in rows
    )


def _languages(modules: Sequence[CodeModuleRow]) -> tuple[LanguageLines, ...]:
    """Agrège les modules par langage, du plus volumineux au moins volumineux."""
    files: dict[str, int] = {}
    lines: dict[str, LineCounts] = {}

    for module in modules:
        files[module.language] = files.get(module.language, 0) + 1
        lines[module.language] = lines.get(module.language, LineCounts()) + LineCounts(
            total=module.lines_total,
            code=module.lines_code,
            comment=module.lines_comment,
            blank=module.lines_blank,
        )

    ranked = sorted(files, key=lambda name: (-lines[name].code, name))
    return tuple(
        LanguageLines(language=name, files=files[name], lines=lines[name]) for name in ranked
    )


def _dependencies(session: Session, audit_id: int) -> tuple[Dependency, ...]:
    """Dépendances déclarées enregistrées pour cet audit."""
    rows = session.scalars(
        select(CodeDependencyRow)
        .where(CodeDependencyRow.audit_id == audit_id)
        .order_by(CodeDependencyRow.ecosystem, CodeDependencyRow.name, CodeDependencyRow.source)
    )
    return tuple(
        Dependency(
            name=row.name,
            ecosystem=row.ecosystem,
            scope=DependencyScope(row.scope),
            specifier=row.specifier,
            source=row.source,
            group=row.group,
        )
        for row in rows
    )


def _tests(session: Session, audit_id: int, modules: Sequence[CodeModuleRow]) -> TestSuite:
    """Reconstitue ce que l'audit sait des tests, depuis les faits enregistrés.

    Le décompte est refait ici plutôt que stocké, exactement comme les autres
    métriques : il se lit des modules marqués comme tests et de leurs fonctions.
    """
    module_ids = [module.id for module in modules]
    test_ids = [module.id for module in modules if module.is_test]

    functions = 0
    if test_ids:
        names = session.scalars(
            select(CodeFunctionRow.name).where(CodeFunctionRow.module_id.in_(test_ids))
        )
        functions = sum(1 for name in names if name.rsplit(".", 1)[-1].startswith("test"))

    frameworks: set[str] = set()

    if module_ids:
        roots = session.scalars(
            select(CodeImportRow.root).where(CodeImportRow.module_id.in_(module_ids)).distinct()
        )
        frameworks.update(
            label for root in roots if (label := FRAMEWORK_IMPORTS.get(root)) is not None
        )

    declared = session.scalars(
        select(CodeDependencyRow.name).where(CodeDependencyRow.audit_id == audit_id)
    )
    for name in declared:
        lowered = name.lower()
        frameworks.update(
            label for marker, label in FRAMEWORK_PACKAGES.items() if marker in lowered
        )

    return TestSuite(
        files=len(test_ids),
        functions=functions,
        frameworks=tuple(sorted(frameworks)),
        directories=find_test_directories(module.path for module in modules),
    )
