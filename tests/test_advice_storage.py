"""Tests de la persistance des recommandations du conseiller IA (étape 26).

Comme un audit ou un snapshot, une exécution du conseiller s'ajoute : deux
appels successifs produisent deux lignes distinctes, jamais un remplacement.
"""

from datetime import UTC, datetime, timedelta

import pytest

from githor.models.advice import Advice, AdviceItem
from githor.models.repository import Repository
from githor.storage.advice import count_advice_runs, latest_advice, save_advice
from githor.storage.database import Database
from githor.storage.repositories import upsert_repository

MOMENT = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


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


def advice(**overrides: object) -> Advice:
    """Construit des recommandations de test."""
    defaults: dict[str, object] = {
        "generated_at": MOMENT,
        "model": "llama3.1",
        "items": (
            AdviceItem(
                rank=1,
                source_rule="development.tests",
                title="Ajouter des tests",
                recommendation="Créer un répertoire tests/.",
            ),
            AdviceItem(
                rank=2,
                source_rule="documentation.license",
                title="Ajouter une licence",
                recommendation="Ajouter un fichier LICENSE.",
            ),
        ),
    }
    return Advice(**{**defaults, **overrides})  # type: ignore[arg-type]


def test_advice_is_persisted_with_its_items(database: Database, repository_id: int) -> None:
    with database.session() as session:
        row = save_advice(session, repository_id, advice())

        assert row.id is not None
        assert row.model == "llama3.1"
        assert not row.degraded
        assert len(row.items) == 2
        assert [item.source_rule for item in row.items] == [
            "development.tests",
            "documentation.license",
        ]


def test_latest_advice_returns_the_most_recent_run(database: Database, repository_id: int) -> None:
    with database.session() as session:
        save_advice(session, repository_id, advice(generated_at=MOMENT))
        save_advice(session, repository_id, advice(generated_at=MOMENT + timedelta(days=1)))

    with database.session() as session:
        latest = latest_advice(session, repository_id)

    assert latest is not None
    assert latest.generated_at == MOMENT + timedelta(days=1)


def test_two_runs_are_never_merged_into_one(database: Database, repository_id: int) -> None:
    """Un nouvel appel à githor advise ajoute une entrée, il n'écrase jamais la précédente."""
    with database.session() as session:
        save_advice(session, repository_id, advice(generated_at=MOMENT))
        save_advice(session, repository_id, advice(generated_at=MOMENT + timedelta(days=1)))

    with database.session() as session:
        assert count_advice_runs(session, repository_id) == 2


def test_a_degraded_run_keeps_a_single_unattributed_item(
    database: Database, repository_id: int
) -> None:
    degraded = advice(
        items=(
            AdviceItem(
                rank=1,
                source_rule=None,
                title="Recommandations",
                recommendation="Texte brut non structuré.",
            ),
        ),
        degraded=True,
    )

    with database.session() as session:
        row = save_advice(session, repository_id, degraded)

        assert row.degraded
        assert row.items[0].source_rule is None


def test_a_repository_without_a_run_has_no_latest_advice(
    database: Database, repository_id: int
) -> None:
    with database.session() as session:
        assert latest_advice(session, repository_id) is None
        assert count_advice_runs(session, repository_id) == 0
