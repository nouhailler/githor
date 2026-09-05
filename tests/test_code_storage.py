"""Tests de la persistance des audits de code (étape 17).

Ce qui est vérifié ici, c'est surtout une propriété : **aucune métrique n'est
stockée**. Toutes se recalculent à la lecture depuis les faits enregistrés, ce
qui garantit qu'un chiffre ne peut pas diverger de ce dont il est tiré.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from githor.models.code import (
    ClassAnalysis,
    CodeAudit,
    Dependency,
    DependencyScope,
    FunctionAnalysis,
    Import,
    ImportKind,
    LineCounts,
    ModuleAnalysis,
)
from githor.models.repository import Repository
from githor.storage.code import (
    AuditMetrics,
    audit_metrics,
    count_audits,
    first_audit,
    last_analysed_at,
    latest_audit,
    save_audit,
)
from githor.storage.database import Database
from githor.storage.repositories import upsert_repository

MOMENT = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


@pytest.fixture
def database() -> Database:
    """Base en mémoire, schéma créé."""
    instance = Database.in_memory()
    instance.create_schema()
    return instance


@pytest.fixture
def repository_id(database: Database) -> int:
    """Enregistre un repository et retourne son identifiant interne."""
    with database.session() as session:
        row, _ = upsert_repository(
            session,
            Repository(
                github_id=1,
                name="depot",
                full_name="proprio/depot",
                owner="proprio",
                html_url="https://github.com/proprio/depot",
            ),
        )
        return row.id


def audit(**overrides: Any) -> CodeAudit:
    """Construit un audit de test."""
    defaults: dict[str, Any] = {
        "analysed_at": MOMENT,
        "commit": "0123456789abcdef",
        "branch": "main",
        "files_seen": 4,
        "files_analysed": 2,
        "files_binary": 1,
        "files_too_large": 1,
        "modules": (
            ModuleAnalysis(
                path="src/app.py",
                language="Python",
                size_bytes=200,
                lines=LineCounts(total=10, code=7, comment=2, blank=1),
                functions=(
                    FunctionAnalysis(name="traiter", line=3, complexity=4, arguments=2),
                    FunctionAnalysis(name="Service.lire", line=8, complexity=2),
                ),
                classes=(ClassAnalysis(name="Service", line=6, methods=1),),
                imports=(
                    Import(module="os", kind=ImportKind.STDLIB, line=1),
                    Import(module="httpx", kind=ImportKind.THIRD_PARTY, line=2),
                ),
                has_docstring=True,
            ),
            ModuleAnalysis(
                path="tests/test_app.py",
                language="Python",
                lines=LineCounts(total=5, code=4, comment=0, blank=1),
                functions=(FunctionAnalysis(name="test_traiter", line=2),),
                imports=(Import(module="pytest", kind=ImportKind.THIRD_PARTY, line=1),),
                is_test=True,
            ),
        ),
        "dependencies": (
            Dependency(name="httpx", ecosystem="PyPI", specifier=">=0.27", source="pyproject.toml"),
            Dependency(
                name="pytest",
                ecosystem="PyPI",
                scope=DependencyScope.DEVELOPMENT,
                source="pyproject.toml",
            ),
        ),
    }
    return CodeAudit(**{**defaults, **overrides})


def stored(database: Database, repository_id: int, **overrides: Any) -> AuditMetrics:
    """Enregistre un audit et retourne ses métriques relues."""
    with database.session() as session:
        row = save_audit(session, repository_id, audit(**overrides))
        return audit_metrics(session, row)


# ── Écriture ─────────────────────────────────────────────────────────────────


def test_an_audit_is_stored_with_everything_it_found(
    database: Database, repository_id: int
) -> None:
    metrics = stored(database, repository_id)

    assert metrics.commit == "0123456789abcdef"
    assert metrics.files_analysed == 2
    assert metrics.function_count == 3
    assert metrics.class_count == 1


def test_an_audit_is_added_never_replaced(database: Database, repository_id: int) -> None:
    """Deux analyses successives se comparent ; elles ne s'écrasent pas."""
    with database.session() as session:
        save_audit(session, repository_id, audit())
        save_audit(session, repository_id, audit(analysed_at=MOMENT + timedelta(days=1)))

    with database.session() as session:
        assert count_audits(session, repository_id) == 2


def test_the_latest_audit_is_the_most_recent(database: Database, repository_id: int) -> None:
    later = MOMENT + timedelta(days=1)
    with database.session() as session:
        save_audit(session, repository_id, audit(analysed_at=later, commit="ffff"))
        save_audit(session, repository_id, audit())

    with database.session() as session:
        newest = latest_audit(session, repository_id)
        oldest = first_audit(session, repository_id)

    assert newest is not None
    assert newest.commit == "ffff"
    assert oldest is not None
    assert oldest.analysed_at == MOMENT


def test_dates_come_back_in_utc(database: Database, repository_id: int) -> None:
    """SQLite ne conserve pas le fuseau : sans UTCDateTime, l'historique serait faux."""
    with database.session() as session:
        save_audit(session, repository_id, audit())

    with database.session() as session:
        row = latest_audit(session, repository_id)

    assert row is not None
    assert row.analysed_at == MOMENT
    assert row.analysed_at.tzinfo is not None


def test_a_repository_without_audit_has_none(database: Database, repository_id: int) -> None:
    with database.session() as session:
        assert latest_audit(session, repository_id) is None
        assert count_audits(session, repository_id) == 0


def test_last_analysed_at_answers_in_one_query(database: Database, repository_id: int) -> None:
    later = MOMENT + timedelta(days=2)
    with database.session() as session:
        save_audit(session, repository_id, audit())
        save_audit(session, repository_id, audit(analysed_at=later))

    with database.session() as session:
        assert last_analysed_at(session, [repository_id]) == {repository_id: later}


def test_last_analysed_at_of_nothing_queries_nothing(database: Database) -> None:
    with database.session() as session:
        assert last_analysed_at(session, []) == {}


def test_deleting_a_repository_takes_its_audits_along(
    database: Database, repository_id: int
) -> None:
    """Les clés étrangères sont réellement appliquées : rien ne doit rester orphelin."""
    from sqlalchemy import func, select

    from githor.storage.tables import CodeFunctionRow, RepositoryRow

    with database.session() as session:
        save_audit(session, repository_id, audit())

    with database.session() as session:
        session.delete(session.get(RepositoryRow, repository_id))

    with database.session() as session:
        remaining = session.scalar(select(func.count()).select_from(CodeFunctionRow))

    assert remaining == 0


# ── Métriques dérivées ───────────────────────────────────────────────────────


def test_lines_are_recomputed_from_the_modules(database: Database, repository_id: int) -> None:
    metrics = stored(database, repository_id)

    assert metrics.lines == LineCounts(total=15, code=11, comment=2, blank=2)


def test_complexity_statistics_are_recomputed(database: Database, repository_id: int) -> None:
    metrics = stored(database, repository_id)

    # (4 + 2 + 1) / 3
    assert metrics.average_complexity == 2.33
    assert metrics.max_complexity == 4


def test_a_repository_without_functions_has_no_average(
    database: Database, repository_id: int
) -> None:
    """Un dépôt sans fonction n'a pas une complexité de zéro : il n'en a pas."""
    metrics = stored(
        database,
        repository_id,
        modules=(ModuleAnalysis(path="README.md", language="Markdown"),),
    )

    assert metrics.average_complexity is None
    assert metrics.max_complexity == 0


def test_the_most_complex_functions_name_their_file(database: Database, repository_id: int) -> None:
    metrics = stored(database, repository_id)
    first = metrics.most_complex[0]

    assert first.name == "traiter"
    assert first.path == "src/app.py"
    assert first.line == 3


def test_languages_are_recomputed_and_ranked(database: Database, repository_id: int) -> None:
    metrics = stored(database, repository_id)

    assert [item.language for item in metrics.languages] == ["Python"]
    assert metrics.languages[0].files == 2


def test_dependencies_come_back_with_their_scope(database: Database, repository_id: int) -> None:
    metrics = stored(database, repository_id)

    runtime = metrics.dependencies_in_scope(DependencyScope.RUNTIME)
    development = metrics.dependencies_in_scope(DependencyScope.DEVELOPMENT)

    assert [item.name for item in runtime] == ["httpx"]
    assert [item.name for item in development] == ["pytest"]
    assert runtime[0].source == "pyproject.toml"


def test_the_test_suite_is_recomputed_from_the_stored_facts(
    database: Database, repository_id: int
) -> None:
    metrics = stored(database, repository_id)

    assert metrics.tests.files == 1
    assert metrics.tests.functions == 1
    assert metrics.tests.frameworks == ("pytest",)
    assert metrics.tests.directories == ("tests",)


def test_skipped_files_are_kept_because_nothing_else_records_them(
    database: Database, repository_id: int
) -> None:
    """Ces deux décomptes n'ont pas de ligne à eux : ils ne pourraient pas être recomptés."""
    metrics = stored(database, repository_id)

    assert metrics.files_binary == 1
    assert metrics.files_too_large == 1
    assert metrics.files_seen == 4


def test_parse_errors_are_counted(database: Database, repository_id: int) -> None:
    metrics = stored(
        database,
        repository_id,
        modules=(
            ModuleAnalysis(path="cassé.py", language="Python", parse_error="ligne 1 : invalide"),
        ),
    )

    assert metrics.parse_errors == 1
