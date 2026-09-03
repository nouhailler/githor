"""Tests des exports (étape 11) : jeu de données, JSON, CSV, Markdown.

Les exports se construisent depuis SQLite : aucun appel réseau n'est nécessaire,
et aucun n'est autorisé.
"""

import csv
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from githor.collectors.repositories import build_snapshot
from githor.errors import StorageError
from githor.exporters import ExportFormat, build_dataset, export_filename, render, write_export
from githor.exporters.csv_format import COLUMNS
from githor.models.activity import Commit
from githor.models.finding import Finding, Severity, Status
from githor.models.issue import Issue
from githor.models.release import Release
from githor.models.repository import Repository
from githor.models.snapshot import Language, RepositoryFile
from githor.storage.database import Database
from githor.storage.findings import save_findings
from githor.storage.repositories import (
    add_snapshot,
    save_commits,
    save_files,
    save_issues,
    save_languages,
    save_releases,
    upsert_repository,
)

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


@pytest.fixture
def database() -> Database:
    """Base en mémoire, schéma créé."""
    instance = Database.in_memory()
    instance.create_schema()
    return instance


@pytest.fixture
def populated(database: Database) -> Database:
    """Base contenant un repository complet et un dépôt jamais scanné."""
    repository = Repository(
        github_id=1,
        name="Architecturor",
        full_name="nouhailler/Architecturor",
        owner="nouhailler",
        html_url="https://github.com/nouhailler/Architecturor",
        description="Appli | pour architectes",
        language="TypeScript",
        stars=3,
        forks=1,
        watchers=3,
        open_issues_count=2,
        size_kb=4096,
        pushed_at=NOW - timedelta(days=2),
    )

    with database.session() as session:
        row, _ = upsert_repository(session, repository)
        snapshot = add_snapshot(
            session, row.id, build_snapshot(repository, collected_at=NOW, open_prs=1)
        )
        save_languages(
            session,
            snapshot.id,
            [
                Language(language="TypeScript", bytes=900, percentage=90.0),
                Language(language="CSS", bytes=100, percentage=10.0),
            ],
        )
        save_files(
            session,
            snapshot.id,
            [
                RepositoryFile(path="README.md", type="blob", size=120),
                RepositoryFile(path="src", type="tree"),
                RepositoryFile(path="src/main.ts", type="blob", size=400),
            ],
        )
        save_commits(
            session,
            row.id,
            [
                Commit(sha="a" * 40, committed_at=NOW - timedelta(days=2)),
                Commit(sha="b" * 40, committed_at=NOW - timedelta(days=60)),
                Commit(sha="c" * 40, committed_at=NOW - timedelta(days=200)),
            ],
        )
        save_releases(session, row.id, [Release(tag="v0.1.0", name="Première", published_at=NOW)])
        save_issues(
            session,
            row.id,
            [
                Issue(github_id=10, number=1, title="Ouverte", state="open"),
                Issue(github_id=11, number=2, title="Fermée", state="closed"),
            ],
        )
        save_findings(
            session,
            row.id,
            snapshot.id,
            [
                Finding(
                    category="documentation",
                    rule="documentation.readme",
                    severity=Severity.INFO,
                    status=Status.OK,
                    message="README présent : README.md.",
                ),
                Finding(
                    category="development",
                    rule="development.tests",
                    severity=Severity.HIGH,
                    status=Status.OPEN,
                    message="tests/ absent.",
                    recommendation="Ajouter un répertoire de tests.",
                ),
            ],
        )

        # Un dépôt connu mais jamais scanné : l'export doit le supporter.
        upsert_repository(
            session,
            Repository(
                github_id=2,
                name="Vide",
                full_name="nouhailler/Vide",
                owner="nouhailler",
                html_url="https://github.com/nouhailler/Vide",
            ),
        )
    return database


def dataset_of(database: Database):  # noqa: ANN201 — type interne au module
    """Construit le jeu de données d'une base."""
    with database.session() as session:
        return build_dataset(session, generated_at=NOW)


# ── Jeu de données et métriques ──────────────────────────────────────────────


def test_the_dataset_covers_every_stored_repository(populated: Database) -> None:
    dataset = dataset_of(populated)

    assert dataset.repository_count == 2
    assert [repository.full_name for repository in dataset.repositories] == [
        "nouhailler/Architecturor",
        "nouhailler/Vide",
    ]


def test_metrics_are_derived_from_the_latest_snapshot(populated: Database) -> None:
    metrics = dataset_of(populated).repositories[0].metrics

    assert metrics.files == 2
    assert metrics.directories == 1
    assert metrics.languages == 2
    assert metrics.releases == 1
    assert metrics.open_issues == 1
    assert metrics.closed_issues == 1


def test_commit_windows_are_counted_from_the_snapshot_date(populated: Database) -> None:
    """Compter depuis la date du snapshot rend l'export reproductible."""
    metrics = dataset_of(populated).repositories[0].metrics

    assert metrics.commits_30_days == 1
    assert metrics.commits_90_days == 2
    assert metrics.last_commit_at == NOW - timedelta(days=2)


def test_open_findings_are_counted_by_severity(populated: Database) -> None:
    metrics = dataset_of(populated).repositories[0].metrics

    assert metrics.findings_open == 1
    assert metrics.findings_high == 1
    assert metrics.findings_medium == 0


def test_a_repository_without_snapshot_exports_empty_metrics(populated: Database) -> None:
    empty = dataset_of(populated).repositories[1]

    assert empty.snapshot is None
    assert empty.metrics.files == 0
    assert empty.languages == ()


def test_an_empty_database_produces_an_empty_dataset(database: Database) -> None:
    dataset = dataset_of(database)

    assert dataset.repository_count == 0
    assert dataset.repositories == ()


# ── JSON ─────────────────────────────────────────────────────────────────────


def test_the_json_export_is_valid_and_complete(populated: Database) -> None:
    payload = json.loads(render(dataset_of(populated), ExportFormat.JSON))

    assert payload["repository_count"] == 2
    repository = payload["repositories"][0]
    assert repository["full_name"] == "nouhailler/Architecturor"
    assert repository["snapshot"]["open_prs"] == 1
    assert [language["language"] for language in repository["languages"]] == ["TypeScript", "CSS"]
    assert len(repository["findings"]) == 2
    assert repository["releases"][0]["tag"] == "v0.1.0"


def test_the_json_export_writes_dates_in_utc(populated: Database) -> None:
    payload = json.loads(render(dataset_of(populated), ExportFormat.JSON))

    assert payload["generated_at"].endswith("Z")
    assert payload["repositories"][0]["snapshot"]["collected_at"].startswith("2026-09-03T12:00")


# ── CSV ──────────────────────────────────────────────────────────────────────


def test_the_csv_export_is_valid_and_flat(populated: Database) -> None:
    rows = list(csv.DictReader(io.StringIO(render(dataset_of(populated), ExportFormat.CSV))))

    assert len(rows) == 2
    assert list(rows[0]) == list(COLUMNS)
    assert rows[0]["repository"] == "nouhailler/Architecturor"
    assert rows[0]["files"] == "2"
    assert rows[0]["languages"] == "2"
    assert rows[0]["findings_high"] == "1"


def test_the_csv_export_leaves_unmeasured_cells_empty(populated: Database) -> None:
    rows = list(csv.DictReader(io.StringIO(render(dataset_of(populated), ExportFormat.CSV))))

    assert rows[1]["repository"] == "nouhailler/Vide"
    assert rows[1]["stars"] == ""
    assert rows[1]["snapshot"] == ""


# ── Markdown ─────────────────────────────────────────────────────────────────


def test_the_markdown_export_is_readable_and_structured(populated: Database) -> None:
    document = render(dataset_of(populated), ExportFormat.MARKDOWN)

    assert document.startswith("# Inventaire Githor")
    assert "## Vue d'ensemble" in document
    assert "## Ce qui manque le plus souvent" in document
    assert "### nouhailler/Architecturor" in document
    assert "tests/ absent." in document
    assert "TypeScript 90.0 %" in document


def test_the_markdown_export_escapes_what_would_break_a_table(populated: Database) -> None:
    """Une description contenant une barre verticale ne doit pas casser le tableau."""
    document = render(dataset_of(populated), ExportFormat.MARKDOWN)

    assert "Appli \\| pour architectes" in document


def test_the_markdown_export_ranks_recurring_findings(populated: Database) -> None:
    document = render(dataset_of(populated), ExportFormat.MARKDOWN)
    section = document.split("## Ce qui manque le plus souvent")[1]

    assert "`development.tests`" in section
    assert "Élevée" in section


# ── Écriture des fichiers ────────────────────────────────────────────────────


def test_the_export_filename_is_timestamped(populated: Database) -> None:
    assert export_filename(ExportFormat.JSON, moment=NOW) == "githor-20260903-120000.json"
    assert export_filename(ExportFormat.MARKDOWN, moment=NOW) == "githor-20260903-120000.md"


def test_writing_an_export_creates_the_directory(populated: Database, tmp_path: Path) -> None:
    directory = tmp_path / "exports"

    path = write_export(dataset_of(populated), ExportFormat.JSON, directory)

    assert path.parent == directory
    assert json.loads(path.read_text(encoding="utf-8"))["repository_count"] == 2


def test_an_export_never_overwrites_the_previous_one(populated: Database, tmp_path: Path) -> None:
    dataset = dataset_of(populated)

    first = write_export(dataset, ExportFormat.CSV, tmp_path, moment=NOW)
    second = write_export(dataset, ExportFormat.CSV, tmp_path, moment=NOW + timedelta(seconds=1))

    assert first != second
    assert first.exists() and second.exists()


def test_an_unwritable_directory_is_reported_clearly(populated: Database, tmp_path: Path) -> None:
    blocked = tmp_path / "fichier"
    blocked.write_text("ceci n'est pas un répertoire", encoding="utf-8")

    with pytest.raises(StorageError, match="Export impossible"):
        write_export(dataset_of(populated), ExportFormat.JSON, blocked / "exports")
