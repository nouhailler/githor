"""Tests des rapports individuels (étape 12).

Un rapport se construit depuis SQLite, comme un export : aucun appel réseau
n'est nécessaire, et aucun n'est autorisé.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from githor import __version__
from githor.collectors.repositories import build_snapshot
from githor.errors import StorageError
from githor.models.activity import Commit
from githor.models.code import (
    CodeAudit,
    Dependency,
    FunctionAnalysis,
    Import,
    ImportKind,
    LineCounts,
    ModuleAnalysis,
)
from githor.models.finding import Finding, Severity, Status
from githor.models.issue import Issue
from githor.models.release import Release
from githor.models.repository import Repository
from githor.models.snapshot import Language, RepositoryFile
from githor.reports import Report, build_report, render_report, report_filename, write_report
from githor.storage.code import save_audit
from githor.storage.database import Database
from githor.storage.findings import save_findings
from githor.storage.repositories import (
    add_snapshot,
    find_repository_by_name,
    save_commits,
    save_files,
    save_issues,
    save_languages,
    save_releases,
    upsert_repository,
)

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)

ARCHITECTUROR = Repository(
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

FINDINGS = (
    Finding(
        category="documentation",
        rule="documentation.readme",
        severity=Severity.INFO,
        status=Status.OK,
        message="README présent : README.md.",
    ),
    Finding(
        category="documentation",
        rule="documentation.changelog",
        severity=Severity.MEDIUM,
        status=Status.OPEN,
        message="CHANGELOG absent.",
        recommendation="Ajouter un CHANGELOG.md.",
    ),
    Finding(
        category="development",
        rule="development.tests",
        severity=Severity.HIGH,
        status=Status.OPEN,
        message="tests/ absent.",
        recommendation="Ajouter un répertoire de tests.",
    ),
)


@pytest.fixture
def database() -> Database:
    """Base en mémoire, schéma créé."""
    instance = Database.in_memory()
    instance.create_schema()
    return instance


@pytest.fixture
def populated(database: Database) -> Database:
    """Base contenant un repository complet et un dépôt jamais scanné."""
    with database.session() as session:
        row, _ = upsert_repository(session, ARCHITECTUROR)
        snapshot = add_snapshot(
            session, row.id, build_snapshot(ARCHITECTUROR, collected_at=NOW, open_prs=1)
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
        save_commits(session, row.id, [Commit(sha="a" * 40, committed_at=NOW - timedelta(days=2))])
        save_releases(
            session,
            row.id,
            [
                Release(tag="v0.2.0", name="Deuxième", published_at=NOW, prerelease=True),
                Release(tag="v0.1.0", name="Première", published_at=NOW - timedelta(days=30)),
            ],
        )
        save_issues(
            session,
            row.id,
            [
                Issue(github_id=10, number=1, title="Ouverte", state="open"),
                Issue(github_id=11, number=2, title="Fermée", state="closed"),
            ],
        )
        save_findings(session, row.id, snapshot.id, list(FINDINGS))

        # Un dépôt connu mais jamais scanné : le rapport doit le supporter.
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


def report_of(database: Database, name: str) -> Report:
    """Construit le rapport d'un dépôt désigné par son nom."""
    with database.session() as session:
        row = find_repository_by_name(session, name)
        assert row is not None
        return build_report(session, row, generated_at=NOW)


def rendered(database: Database, name: str = "Architecturor") -> str:
    """Rend le rapport d'un dépôt."""
    return render_report(report_of(database, name))


# ── Construction du rapport ──────────────────────────────────────────────────


def test_the_report_describes_the_latest_snapshot(populated: Database) -> None:
    report = report_of(populated, "Architecturor")

    assert report.githor_version == __version__
    assert report.generated_at == NOW
    assert report.repository.full_name == "nouhailler/Architecturor"
    assert report.repository.snapshot is not None
    assert report.repository.snapshot.collected_at == NOW


def test_the_report_counts_the_snapshots_kept(populated: Database) -> None:
    """L'historique conservé se lit dans le rapport : c'est lui qui rendra la
    comparaison de deux dates possible."""
    with populated.session() as session:
        row = find_repository_by_name(session, "Architecturor")
        assert row is not None
        add_snapshot(
            session,
            row.id,
            build_snapshot(ARCHITECTUROR, collected_at=NOW + timedelta(days=1), open_prs=1),
        )

    report = report_of(populated, "Architecturor")

    assert report.snapshots == 2
    assert report.first_snapshot_at == NOW


def test_metrics_come_from_the_shared_dataset(populated: Database) -> None:
    """Le rapport décrit un dépôt exactement comme l'export décrit chacun des siens."""
    metrics = report_of(populated, "Architecturor").repository.metrics

    assert metrics.files == 2
    assert metrics.directories == 1
    assert metrics.languages == 2
    assert metrics.releases == 2
    assert metrics.open_issues == 1
    assert metrics.closed_issues == 1
    assert metrics.findings_open == 2


# ── Rendu Markdown ───────────────────────────────────────────────────────────


def test_the_report_opens_on_the_repository_name(populated: Database) -> None:
    document = rendered(populated)

    assert document.startswith("# nouhailler/Architecturor\n")
    assert document.endswith("\n")


def test_the_report_states_where_its_numbers_come_from(populated: Database) -> None:
    document = rendered(populated)

    assert f"Rapport Githor {__version__}" in document
    assert "d'après le snapshot du 2026-09-03 12:00 UTC" in document


def test_the_overview_reports_the_measured_values(populated: Database) -> None:
    document = rendered(populated)

    assert "| Files | 2 |" in document
    assert "| Directories | 1 |" in document
    assert "| Open issues | 1 |" in document
    assert "| Closed issues | 1 |" in document
    assert "| Open pull requests | 1 |" in document
    assert "| Releases | 2 |" in document


def test_languages_are_listed_from_the_heaviest(populated: Database) -> None:
    document = rendered(populated)
    languages = [line for line in document.splitlines() if line.startswith("- TypeScript")]

    assert languages == ["- TypeScript — 90.0 % (900 o)"]
    assert "- CSS — 10.0 % (100 o)" in document


def test_checks_show_what_is_there_and_what_is_missing(populated: Database) -> None:
    document = rendered(populated)

    assert "## Documentation" in document
    assert "| README | ✓ |" in document
    assert "| CHANGELOG | ✗ |" in document
    assert "| tests/ | ✗ |" in document


def test_checks_follow_the_catalog_order(populated: Database) -> None:
    """Le catalogue va du plus attendu au plus accessoire ; le rapport le respecte."""
    lines = rendered(populated).splitlines()

    assert lines.index("| README | ✓ |") < lines.index("| CHANGELOG | ✗ |")


def test_open_findings_are_grouped_by_severity(populated: Database) -> None:
    document = rendered(populated)

    assert "### Élevée" in document
    assert "- tests/ absent. (`development.tests`) — *Ajouter un répertoire de tests.*" in document
    assert "### Moyenne" in document
    # Les règles satisfaites ne sont pas des constats : elles restent au tableau.
    assert "README présent" not in document.split("## Constats")[1]


def test_releases_are_listed_with_their_state(populated: Database) -> None:
    document = rendered(populated)

    assert "| `v0.2.0` | Deuxième | 2026-09-03 12:00 UTC | préversion |" in document
    assert "| `v0.1.0` | Première | 2026-08-04 12:00 UTC | publiée |" in document


def test_a_pipe_in_a_description_does_not_break_a_table(populated: Database) -> None:
    assert "Appli \\| pour architectes" in rendered(populated)


def test_a_repository_without_a_snapshot_says_what_to_do(populated: Database) -> None:
    document = rendered(populated, "Vide")

    assert "Aucun snapshot enregistré" in document
    assert "githor scan Vide" in document
    assert "## Vue d'ensemble" not in document


def test_a_single_snapshot_is_announced_as_such(populated: Database) -> None:
    assert "Premier snapshot" in rendered(populated)


# ── Écriture du fichier ──────────────────────────────────────────────────────


def test_the_filename_is_timestamped_and_flattened() -> None:
    name = report_filename("nouhailler/Architecturor", moment=NOW)

    assert name == "githor-report-nouhailler-Architecturor-20260903-120000.md"


def test_writing_to_a_directory_creates_a_timestamped_file(
    populated: Database, tmp_path: Path
) -> None:
    path = write_report(report_of(populated, "Architecturor"), tmp_path)

    assert path.parent == tmp_path
    assert path.name == "githor-report-nouhailler-Architecturor-20260903-120000.md"
    assert path.read_text(encoding="utf-8").startswith("# nouhailler/Architecturor")


def test_writing_to_a_file_honours_the_given_name(populated: Database, tmp_path: Path) -> None:
    destination = tmp_path / "sous" / "rapport.md"

    path = write_report(report_of(populated, "Architecturor"), destination)

    assert path == destination
    assert destination.read_text(encoding="utf-8").startswith("# nouhailler/Architecturor")


def test_an_unwritable_destination_raises_a_storage_error(
    populated: Database, tmp_path: Path
) -> None:
    obstacle = tmp_path / "obstacle"
    obstacle.write_text("ce n'est pas un répertoire", encoding="utf-8")

    with pytest.raises(StorageError, match="Rapport impossible"):
        write_report(report_of(populated, "Architecturor"), obstacle / "rapport.md")


# ── Section « Code » ─────────────────────────────────────────────────────────


def audited(database: Database, name: str = "Architecturor") -> Database:
    """Ajoute un audit de code au dépôt désigné."""
    with database.session() as session:
        row = find_repository_by_name(session, name)
        assert row is not None
        save_audit(
            session,
            row.id,
            CodeAudit(
                analysed_at=NOW,
                commit="0123456789abcdef",
                branch="main",
                files_seen=3,
                files_analysed=2,
                files_binary=1,
                modules=(
                    ModuleAnalysis(
                        path="src/app.py",
                        language="Python",
                        lines=LineCounts(total=10, code=7, comment=2, blank=1),
                        functions=(FunctionAnalysis(name="traiter", line=3, complexity=5),),
                        imports=(Import(module="httpx", kind=ImportKind.THIRD_PARTY, line=1),),
                    ),
                    ModuleAnalysis(
                        path="tests/test_app.py",
                        language="Python",
                        lines=LineCounts(total=4, code=3, comment=0, blank=1),
                        functions=(FunctionAnalysis(name="test_traiter", line=1),),
                        is_test=True,
                    ),
                ),
                dependencies=(
                    Dependency(
                        name="httpx",
                        ecosystem="PyPI",
                        specifier=">=0.27",
                        source="pyproject.toml",
                    ),
                ),
            ),
        )
    return database


def test_a_report_without_an_audit_says_what_to_run(populated: Database) -> None:
    document = rendered(populated)

    assert "## Code" in document
    assert "githor audit Architecturor" in document


def test_a_report_carries_the_latest_audit(populated: Database) -> None:
    document = rendered(audited(populated))

    assert "commit `0123456`" in document
    assert "| Lines of code | 10 |" in document
    assert "| Functions | 2 |" in document
    assert "| Test files | 1 |" in document


def test_the_code_section_names_the_analysed_commit(populated: Database) -> None:
    """Une mesure indatable ne se compare à rien."""
    report = report_of(audited(populated), "Architecturor")

    assert report.code is not None
    assert report.code.commit == "0123456789abcdef"
    assert report.audits == 1


def test_the_code_section_cites_the_file_of_a_complex_function(populated: Database) -> None:
    document = rendered(audited(populated))

    assert "`src/app.py`:3" in document


def test_the_code_section_cites_the_manifest_of_a_dependency(populated: Database) -> None:
    document = rendered(audited(populated))

    assert "| httpx | >=0.27 | exécution | PyPI | `pyproject.toml` |" in document


def test_skipped_files_are_reported_in_the_code_section(populated: Database) -> None:
    """Un fichier écarté en silence fausserait tous les décomptes."""
    document = rendered(audited(populated))

    assert "Fichiers écartés : 1 binaire(s)." in document


def test_an_audit_is_shown_even_without_a_snapshot(populated: Database) -> None:
    """L'audit se lit sur un clone local : il ne suppose aucun scan préalable."""
    document = rendered(audited(populated, "Vide"), "Vide")

    assert "Aucun snapshot enregistré" in document
    assert "## Code" in document
    assert "| Lines of code | 10 |" in document
