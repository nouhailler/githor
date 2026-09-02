"""Tests du stockage SQLite (étape 7) : schéma, contraintes, dates, historique."""

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from githor.collectors.repositories import build_snapshot
from githor.errors import GithorError, StorageError
from githor.models.repository import Repository
from githor.storage.database import Database
from githor.storage.repositories import (
    add_snapshot,
    count_snapshots,
    latest_snapshot,
    upsert_repository,
)
from githor.storage.tables import (
    CommitRow,
    FindingRow,
    IssueRow,
    LanguageRow,
    ReleaseRow,
    RepositoryFileRow,
    RepositoryRow,
    RepositorySnapshotRow,
)

EXPECTED_TABLES = [
    "commits",
    "findings",
    "issues",
    "languages",
    "releases",
    "repositories",
    "repository_files",
    "repository_snapshots",
]


@pytest.fixture
def database() -> Database:
    """Base en mémoire, schéma créé."""
    instance = Database.in_memory()
    instance.create_schema()
    return instance


def add_repository(database: Database, **overrides: object) -> int:
    """Insère un repository et retourne son identifiant interne."""
    defaults = {
        "github_id": 1,
        "full_name": "nouhailler/Architecturor",
        "name": "Architecturor",
        "owner": "nouhailler",
        "url": "https://github.com/nouhailler/Architecturor",
    }
    row = RepositoryRow(**{**defaults, **overrides})  # type: ignore[arg-type]
    with database.session() as session:
        session.add(row)
        session.flush()
        return row.id


def insert_snapshot(database: Database, repository_id: int, *, collected_at: datetime) -> int:
    """Insère un snapshot et retourne son identifiant interne."""
    row = RepositorySnapshotRow(repository_id=repository_id, collected_at=collected_at)
    with database.session() as session:
        session.add(row)
        session.flush()
        return row.id


# ── Schéma ───────────────────────────────────────────────────────────────────


def test_schema_creates_every_expected_table(database: Database) -> None:
    assert database.table_names() == EXPECTED_TABLES


def test_schema_creation_is_idempotent(database: Database) -> None:
    database.create_schema()
    database.create_schema()

    assert database.table_names() == EXPECTED_TABLES


def test_database_file_and_parents_are_created(tmp_path: Path) -> None:
    path = tmp_path / "sous" / "dossier" / "githor.db"

    with Database(path) as database:
        database.create_schema()

    assert path.exists()


def test_unwritable_location_is_reported(tmp_path: Path) -> None:
    obstacle = tmp_path / "fichier"
    obstacle.write_text("je ne suis pas un répertoire", encoding="utf-8")

    with pytest.raises(StorageError, match="Impossible de créer"):
        Database(obstacle / "githor.db")


def test_storage_errors_are_githor_errors(tmp_path: Path) -> None:
    obstacle = tmp_path / "fichier"
    obstacle.write_text("", encoding="utf-8")

    with pytest.raises(GithorError):
        Database(obstacle / "githor.db")


# ── Intégrité référentielle ──────────────────────────────────────────────────


def test_foreign_keys_are_enforced(database: Database) -> None:
    """SQLite ignore les clés étrangères sans PRAGMA : ce test verrouille son activation."""
    with pytest.raises(StorageError), database.session() as session:
        session.add(RepositorySnapshotRow(repository_id=999, collected_at=datetime.now(UTC)))


def test_deleting_a_repository_cascades(database: Database) -> None:
    repository_id = add_repository(database)
    snapshot_id = insert_snapshot(database, repository_id, collected_at=datetime.now(UTC))

    with database.session() as session:
        session.add_all(
            [
                LanguageRow(
                    snapshot_id=snapshot_id, language="Python", bytes=100, percentage=100.0
                ),
                RepositoryFileRow(snapshot_id=snapshot_id, path="README.md", type="blob", size=12),
                CommitRow(repository_id=repository_id, sha="a" * 40),
                ReleaseRow(repository_id=repository_id, tag="v1.0.0"),
                IssueRow(repository_id=repository_id, github_id=5, number=1, state="open"),
                FindingRow(
                    repository_id=repository_id,
                    snapshot_id=snapshot_id,
                    category="documentation",
                    rule="documentation.changelog",
                    severity="medium",
                    message="CHANGELOG absent",
                ),
            ]
        )

    with database.session() as session:
        session.delete(session.get(RepositoryRow, repository_id))

    with database.session() as session:
        for table in (
            RepositorySnapshotRow,
            LanguageRow,
            RepositoryFileRow,
            CommitRow,
            ReleaseRow,
            IssueRow,
            FindingRow,
        ):
            assert session.scalars(select(table)).all() == [], table.__name__


# ── Contraintes d'unicité ────────────────────────────────────────────────────


def test_github_identifier_is_unique(database: Database) -> None:
    add_repository(database)

    with pytest.raises(StorageError):
        add_repository(database, full_name="nouhailler/Autre", name="Autre")


def test_full_name_is_unique(database: Database) -> None:
    add_repository(database)

    with pytest.raises(StorageError):
        add_repository(database, github_id=2)


def test_a_language_appears_once_per_snapshot(database: Database) -> None:
    snapshot_id = insert_snapshot(
        database, add_repository(database), collected_at=datetime.now(UTC)
    )

    with database.session() as session:
        session.add(LanguageRow(snapshot_id=snapshot_id, language="Python", bytes=1))

    with pytest.raises(StorageError), database.session() as session:
        session.add(LanguageRow(snapshot_id=snapshot_id, language="Python", bytes=2))


def test_a_commit_appears_once_per_repository(database: Database) -> None:
    repository_id = add_repository(database)

    with database.session() as session:
        session.add(CommitRow(repository_id=repository_id, sha="b" * 40))

    with pytest.raises(StorageError), database.session() as session:
        session.add(CommitRow(repository_id=repository_id, sha="b" * 40))


def test_an_issue_number_appears_once_per_repository(database: Database) -> None:
    repository_id = add_repository(database)

    with database.session() as session:
        session.add(IssueRow(repository_id=repository_id, github_id=1, number=7, state="open"))

    with pytest.raises(StorageError), database.session() as session:
        session.add(IssueRow(repository_id=repository_id, github_id=2, number=7, state="closed"))


def test_a_rule_produces_one_finding_per_snapshot(database: Database) -> None:
    repository_id = add_repository(database)
    snapshot_id = insert_snapshot(database, repository_id, collected_at=datetime.now(UTC))
    finding = {
        "repository_id": repository_id,
        "snapshot_id": snapshot_id,
        "category": "documentation",
        "rule": "documentation.readme",
        "severity": "low",
        "message": "README absent",
    }

    with database.session() as session:
        session.add(FindingRow(**finding))

    with pytest.raises(StorageError), database.session() as session:
        session.add(FindingRow(**finding))


# ── Dates ────────────────────────────────────────────────────────────────────


def test_dates_come_back_aware_and_in_utc(database: Database) -> None:
    moment = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    repository_id = add_repository(database, pushed_at=moment)

    with database.session() as session:
        stored = session.get(RepositoryRow, repository_id)
        assert stored is not None
        assert stored.pushed_at is not None
        assert stored.pushed_at.tzinfo is not None
        assert stored.pushed_at == moment


def test_offset_dates_are_normalised_to_utc(database: Database) -> None:
    paris = datetime(2026, 8, 1, 14, 0, tzinfo=timezone(timedelta(hours=2)))
    repository_id = add_repository(database, pushed_at=paris)

    with database.session() as session:
        stored = session.get(RepositoryRow, repository_id)
        assert stored is not None
        assert stored.pushed_at == datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


# ── Historique ───────────────────────────────────────────────────────────────


def test_snapshots_accumulate_instead_of_overwriting(database: Database) -> None:
    """La propriété centrale du modèle : un scan ajoute, il n'écrase pas."""
    repository_id = add_repository(database)
    dates = [
        datetime(2026, 8, 1, tzinfo=UTC),
        datetime(2026, 8, 15, tzinfo=UTC),
        datetime(2026, 9, 2, tzinfo=UTC),
    ]

    for collected_at in dates:
        insert_snapshot(database, repository_id, collected_at=collected_at)

    with database.session() as session:
        stored = session.scalars(
            select(RepositorySnapshotRow).order_by(RepositorySnapshotRow.collected_at)
        ).all()

    assert [snapshot.collected_at for snapshot in stored] == dates


def test_relationships_link_a_repository_to_its_history(database: Database) -> None:
    repository_id = add_repository(database)
    snapshot_id = insert_snapshot(database, repository_id, collected_at=datetime.now(UTC))

    with database.session() as session:
        session.add(LanguageRow(snapshot_id=snapshot_id, language="Python", bytes=42))

    with database.session() as session:
        repository = session.get(RepositoryRow, repository_id)
        assert repository is not None
        assert len(repository.snapshots) == 1
        assert repository.snapshots[0].languages[0].language == "Python"


# ── Transactions ─────────────────────────────────────────────────────────────


def test_a_failed_block_rolls_back(database: Database) -> None:
    with pytest.raises(RuntimeError), database.session() as session:
        session.add(
            RepositoryRow(
                github_id=99,
                full_name="nouhailler/annule",
                name="annule",
                owner="nouhailler",
                url="",
            )
        )
        session.flush()
        raise RuntimeError("échec applicatif")

    with database.session() as session:
        assert session.scalars(select(RepositoryRow)).all() == []


# ── Repositories et snapshots ────────────────────────────────────────────────


def normalised(**overrides: object) -> Repository:
    """Construit un repository normalisé pour les tests d'écriture."""
    defaults = {
        "github_id": 1,
        "name": "Architecturor",
        "full_name": "nouhailler/Architecturor",
        "owner": "nouhailler",
        "html_url": "https://github.com/nouhailler/Architecturor",
        "stars": 3,
        "forks": 1,
        "watchers": 3,
        "open_issues_count": 4,
        "size_kb": 2048,
        "language": "TypeScript",
    }
    return Repository(**{**defaults, **overrides})  # type: ignore[arg-type]


def test_upsert_creates_then_updates(database: Database) -> None:
    with database.session() as session:
        row, created = upsert_repository(session, normalised())
        assert created is True
        assert row.full_name == "nouhailler/Architecturor"

    with database.session() as session:
        row, created = upsert_repository(session, normalised(description="mise à jour"))
        assert created is False
        assert row.description == "mise à jour"

    with database.session() as session:
        assert len(session.scalars(select(RepositoryRow)).all()) == 1


def test_upsert_follows_a_rename(database: Database) -> None:
    """Le dépôt est identifié par son github_id : un renommage ne le duplique pas."""
    with database.session() as session:
        upsert_repository(session, normalised())

    with database.session() as session:
        row, created = upsert_repository(
            session, normalised(name="Nouveau", full_name="nouhailler/Nouveau")
        )

    assert created is False
    assert row.full_name == "nouhailler/Nouveau"

    with database.session() as session:
        assert len(session.scalars(select(RepositoryRow)).all()) == 1


def test_snapshot_carries_the_measured_values(database: Database) -> None:
    repository = normalised()
    moment = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)

    with database.session() as session:
        row, _ = upsert_repository(session, repository)
        snapshot = add_snapshot(session, row.id, build_snapshot(repository, collected_at=moment))

    assert snapshot.collected_at == moment
    assert snapshot.stars == 3
    assert snapshot.open_issues == 4
    assert snapshot.size_kb == 2048
    assert snapshot.primary_language == "TypeScript"
    assert snapshot.open_prs is None


def test_repeated_scans_accumulate_snapshots(database: Database) -> None:
    repository = normalised()
    dates = [datetime(2026, 8, day, tzinfo=UTC) for day in (1, 15, 30)]

    for collected_at in dates:
        with database.session() as session:
            row, _ = upsert_repository(session, repository)
            add_snapshot(session, row.id, build_snapshot(repository, collected_at=collected_at))

    with database.session() as session:
        row = session.scalar(select(RepositoryRow))
        assert row is not None
        assert count_snapshots(session, row.id) == 3
        newest = latest_snapshot(session, row.id)
        assert newest is not None
        assert newest.collected_at == dates[-1]


def test_latest_snapshot_is_none_without_history(database: Database) -> None:
    with database.session() as session:
        row, _ = upsert_repository(session, normalised())
        assert latest_snapshot(session, row.id) is None
        assert count_snapshots(session, row.id) == 0
