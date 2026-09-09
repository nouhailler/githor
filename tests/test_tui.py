"""Tests de l'interface interactive (étapes 33-35, V0.6).

Aucune donnée nouvelle n'est vérifiée ici : la TUI relit ce que
``build_dataset``/``build_report`` savent déjà produire pour la CLI (déjà
testés dans ``test_exporters.py``/``test_reports.py``). Ces tests portent sur
l'assemblage des écrans — ce qui s'affiche, ce qui navigue — pilotés par
``App.run_test()``.
"""

from datetime import UTC, datetime

import pytest

from githor.config import Config, StorageConfig
from githor.models.finding import Finding, Severity, Status
from githor.models.repository import Repository
from githor.models.snapshot import RepositorySnapshot
from githor.storage.database import Database
from githor.storage.findings import save_findings
from githor.storage.repositories import add_snapshot, upsert_repository
from githor.tui.app import GithorApp
from githor.tui.screens import EmptyScreen, RepositoryDetailScreen, RepositoryListScreen

MOMENT = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


def config_for(tmp_path_factory: pytest.TempPathFactory) -> Config:
    """Une configuration dont la base pointe vers un fichier de test."""
    path = tmp_path_factory.mktemp("tui") / "githor.db"
    return Config(storage=StorageConfig(database=path))


def seed(config: Config) -> None:
    """Enregistre deux dépôts, avec un snapshot et des constats variés."""
    database = Database(config.storage.database)
    database.create_schema()
    with database.session() as session:
        good, _ = upsert_repository(
            session,
            Repository(
                github_id=1,
                name="Astror",
                full_name="nouhailler/Astror",
                owner="nouhailler",
                html_url="https://github.com/nouhailler/Astror",
                language="Python",
            ),
        )
        good_snapshot = add_snapshot(session, good.id, RepositorySnapshot(collected_at=MOMENT))
        save_findings(
            session,
            good.id,
            good_snapshot.id,
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

        weak, _ = upsert_repository(
            session,
            Repository(
                github_id=2,
                name="Faible",
                full_name="nouhailler/Faible",
                owner="nouhailler",
                html_url="https://github.com/nouhailler/Faible",
            ),
        )
        weak_snapshot = add_snapshot(session, weak.id, RepositorySnapshot(collected_at=MOMENT))
        save_findings(
            session,
            weak.id,
            weak_snapshot.id,
            [
                Finding(
                    category="documentation",
                    rule="documentation.readme",
                    severity=Severity.HIGH,
                    status=Status.OPEN,
                    message="README absent.",
                    recommendation="Ajouter un README.",
                ),
            ],
        )
    database.close()


async def test_the_list_screen_shows_every_repository_sorted_by_score(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    config = config_for(tmp_path_factory)
    seed(config)
    app = GithorApp(config)

    async with app.run_test() as pilot:
        await pilot.pause()

        assert isinstance(app.screen, RepositoryListScreen)
        table = app.screen.query_one("#repositories")
        rows = [table.get_row_at(index) for index in range(table.row_count)]
        assert rows[0][0] == "nouhailler/Astror"  # score 50 % (un constat sur deux)
        assert rows[1][0] == "nouhailler/Faible"  # score 0 %
        assert rows[0][-1] == "50 %"
        assert rows[1][-1] == "0 %"


async def test_the_filter_narrows_the_list(tmp_path_factory: pytest.TempPathFactory) -> None:
    config = config_for(tmp_path_factory)
    seed(config)
    app = GithorApp(config)

    async with app.run_test() as pilot:
        await pilot.pause()
        filter_input = app.screen.query_one("#filter")
        filter_input.focus()
        await pilot.press(*"faible")
        await pilot.pause()

        table = app.screen.query_one("#repositories")
        assert table.row_count == 1
        assert table.get_row_at(0)[0] == "nouhailler/Faible"


async def test_selecting_a_repository_opens_its_report(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    config = config_for(tmp_path_factory)
    seed(config)
    app = GithorApp(config)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        screen = app.screen
        assert isinstance(screen, RepositoryDetailScreen)
        assert "nouhailler/Astror" in screen._report_markdown
        assert "development.tests" in screen._report_markdown


async def test_escape_returns_to_the_list(tmp_path_factory: pytest.TempPathFactory) -> None:
    config = config_for(tmp_path_factory)
    seed(config)
    app = GithorApp(config)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert isinstance(app.screen, RepositoryListScreen)


async def test_an_empty_database_shows_a_friendly_screen(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    config = config_for(tmp_path_factory)
    with Database(config.storage.database) as database:
        database.create_schema()
    app = GithorApp(config)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, EmptyScreen)
