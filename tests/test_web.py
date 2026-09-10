"""Tests de l'interface web (étapes 37-38, 0.7.0).

Aucune donnée nouvelle n'est vérifiée ici : la page de détail relit ce que
``build_report`` sait déjà produire pour la CLI et la TUI (déjà testé dans
``test_reports.py``). Ces tests portent sur les pages elles-mêmes — ce qui
s'affiche, ce qui répond 404 — pilotés par ``Flask.test_client()``.
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
from githor.web.app import create_app

MOMENT = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def config_for(tmp_path_factory: pytest.TempPathFactory) -> Config:
    """Une configuration dont la base pointe vers un fichier de test."""
    path = tmp_path_factory.mktemp("web") / "githor.db"
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


# ── Liste ────────────────────────────────────────────────────────────────────


def test_the_list_page_shows_every_repository_sorted_by_score(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    config = config_for(tmp_path_factory)
    seed(config)
    client = create_app(config).test_client()

    response = client.get("/")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert body.index("nouhailler/Astror") < body.index("nouhailler/Faible")
    assert "50 %" in body  # Astror : un constat sur deux satisfait
    assert "0 %" in body  # Faible : aucun


def test_the_list_page_links_to_each_repository(tmp_path_factory: pytest.TempPathFactory) -> None:
    config = config_for(tmp_path_factory)
    seed(config)
    client = create_app(config).test_client()

    body = client.get("/").get_data(as_text=True)

    assert "/repos/nouhailler/Astror" in body


def test_an_empty_database_says_what_to_run(tmp_path_factory: pytest.TempPathFactory) -> None:
    config = config_for(tmp_path_factory)
    with Database(config.storage.database) as database:
        database.create_schema()
    client = create_app(config).test_client()

    body = client.get("/").get_data(as_text=True)

    assert "githor scan" in body


# ── Détail ───────────────────────────────────────────────────────────────────


def test_the_detail_page_matches_the_report(tmp_path_factory: pytest.TempPathFactory) -> None:
    config = config_for(tmp_path_factory)
    seed(config)
    client = create_app(config).test_client()

    response = client.get("/repos/nouhailler/Astror")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "nouhailler/Astror" in body
    assert "development.tests" in body
    assert "Ajouter un répertoire de tests." in body


def test_the_detail_page_groups_checks_by_category(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    config = config_for(tmp_path_factory)
    seed(config)
    client = create_app(config).test_client()

    body = client.get("/repos/nouhailler/Astror").get_data(as_text=True)

    assert "Documentation" in body
    assert "check-ok" in body  # README satisfait
    assert "check-missing" in body  # tests/ absent


def test_an_unknown_repository_returns_404(tmp_path_factory: pytest.TempPathFactory) -> None:
    config = config_for(tmp_path_factory)
    seed(config)
    client = create_app(config).test_client()

    response = client.get("/repos/nouhailler/Inconnu")

    assert response.status_code == 404
