"""Tests du moteur de règles et des findings (étape 10).

Les règles ne joignent jamais GitHub : elles lisent un contexte déjà collecté.
Les tests portent donc sur des faits — un marqueur présent ou absent, une date —
et sur le constat produit.
"""

from datetime import UTC, datetime, timedelta

import pytest

from githor.collectors.structure import detect_markers
from githor.models.finding import Finding, Severity, Status
from githor.models.repository import Repository
from githor.models.snapshot import RepositorySnapshot
from githor.rules.base import InactivityRule, MarkerRule, RuleContext
from githor.rules.catalog import CATEGORIES, RULES, rule_labels, rules_by_category
from githor.rules.engine import evaluate, open_findings
from githor.storage.database import Database
from githor.storage.findings import findings_for_snapshot, latest_findings, save_findings
from githor.storage.repositories import add_snapshot, upsert_repository
from githor.storage.tables import FindingRow

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def make_repository(**overrides: object) -> Repository:
    """Construit un repository minimal pour les tests."""
    defaults: dict[str, object] = {
        "github_id": 1,
        "name": "Architecturor",
        "full_name": "nouhailler/Architecturor",
        "owner": "nouhailler",
        "html_url": "https://github.com/nouhailler/Architecturor",
        "pushed_at": NOW - timedelta(days=3),
    }
    return Repository(**{**defaults, **overrides})  # type: ignore[arg-type]


def context_for(*paths: str, **overrides: object) -> RuleContext:
    """Construit un contexte à partir d'une arborescence donnée."""
    repository = overrides.pop("repository", None) or make_repository()
    return RuleContext(
        repository=repository,  # type: ignore[arg-type]
        markers=detect_markers(paths),
        now=NOW,
        **overrides,  # type: ignore[arg-type]
    )


def finding_for(findings: list[Finding], rule: str) -> Finding:
    """Retrouve le constat produit par une règle."""
    return next(finding for finding in findings if finding.rule == rule)


# ── Catalogue ────────────────────────────────────────────────────────────────


def test_rule_identifiers_are_unique() -> None:
    identifiers = [rule.id for rule in RULES]
    assert len(identifiers) == len(set(identifiers))


def test_every_rule_belongs_to_a_known_category() -> None:
    assert {rule.category for rule in RULES} <= set(CATEGORIES)


def test_the_catalog_covers_the_rules_required_by_the_specification() -> None:
    required = {
        "documentation.readme",
        "documentation.license",
        "documentation.changelog",
        "documentation.contributing",
        "documentation.docs",
        "development.tests",
        "development.github_actions",
        "infrastructure.docker",
        "security.dependabot",
        "security.policy",
        "maintenance.activity",
    }
    assert required <= {rule.id for rule in RULES}


def test_rules_are_grouped_in_the_declared_category_order() -> None:
    grouped = rules_by_category()
    assert list(grouped) == list(CATEGORIES)
    assert all(rule.category == category for category, rules in grouped.items() for rule in rules)


def test_every_rule_has_a_label() -> None:
    labels = rule_labels()
    assert len(labels) == len(RULES)
    assert all(label for label in labels.values())


# ── Documentation ────────────────────────────────────────────────────────────


def test_a_present_readme_satisfies_its_rule() -> None:
    finding = finding_for(evaluate(context_for("README.md")), "documentation.readme")

    assert finding.status is Status.OK
    assert finding.severity is Severity.INFO
    assert finding.recommendation is None
    assert "README.md" in finding.message


def test_a_missing_readme_opens_a_high_finding() -> None:
    finding = finding_for(evaluate(context_for("src/main.py")), "documentation.readme")

    assert finding.status is Status.OPEN
    assert finding.severity is Severity.HIGH
    assert finding.recommendation


def test_a_missing_changelog_opens_a_medium_finding() -> None:
    finding = finding_for(evaluate(context_for("README.md")), "documentation.changelog")

    assert finding.status is Status.OPEN
    assert finding.severity is Severity.MEDIUM
    assert "CHANGELOG absent" in finding.message


def test_a_readme_is_detected_whatever_its_case() -> None:
    finding = finding_for(evaluate(context_for("readme.rst")), "documentation.readme")

    assert finding.status is Status.OK


def test_a_docs_directory_satisfies_its_rule() -> None:
    finding = finding_for(evaluate(context_for("docs/index.md")), "documentation.docs")

    assert finding.status is Status.OK


# ── Development et infrastructure ────────────────────────────────────────────


def test_present_tests_satisfy_their_rule() -> None:
    finding = finding_for(evaluate(context_for("tests/test_app.py")), "development.tests")

    assert finding.status is Status.OK
    assert "tests/test_app.py" in finding.message


def test_missing_tests_open_a_high_finding() -> None:
    finding = finding_for(evaluate(context_for("README.md")), "development.tests")

    assert finding.status is Status.OPEN
    assert finding.severity is Severity.HIGH


def test_present_github_actions_satisfy_their_rule() -> None:
    findings = evaluate(context_for(".github/workflows/ci.yml"))

    assert finding_for(findings, "development.github_actions").status is Status.OK


def test_a_github_directory_alone_does_not_satisfy_github_actions() -> None:
    findings = evaluate(context_for(".github/ISSUE_TEMPLATE.md"))

    assert finding_for(findings, "development.github_actions").status is Status.OPEN


def test_a_dockerfile_satisfies_the_docker_rule() -> None:
    assert finding_for(evaluate(context_for("Dockerfile")), "infrastructure.docker").status is (
        Status.OK
    )


def test_a_dependabot_configuration_satisfies_its_rule() -> None:
    findings = evaluate(context_for(".github/dependabot.yml"))

    assert finding_for(findings, "security.dependabot").status is Status.OK


def test_a_security_policy_satisfies_its_rule() -> None:
    findings = evaluate(context_for("SECURITY.md"))

    assert finding_for(findings, "security.policy").status is Status.OK


def test_a_missing_security_policy_opens_a_medium_finding() -> None:
    finding = finding_for(evaluate(context_for("README.md")), "security.policy")

    assert finding.status is Status.OPEN
    assert finding.severity is Severity.MEDIUM


# ── Maintenance ──────────────────────────────────────────────────────────────


def test_a_recently_pushed_repository_satisfies_the_activity_rule() -> None:
    finding = finding_for(evaluate(context_for("README.md")), "maintenance.activity")

    assert finding.status is Status.OK
    assert "3 jour" in finding.message


def test_a_repository_without_push_for_six_months_opens_a_finding() -> None:
    repository = make_repository(pushed_at=NOW - timedelta(days=200))

    finding = finding_for(
        evaluate(context_for("README.md", repository=repository)), "maintenance.activity"
    )

    assert finding.status is Status.OPEN
    assert finding.severity is Severity.MEDIUM
    assert "200 jours" in finding.message


def test_an_archived_repository_is_not_reproached_for_its_inactivity() -> None:
    repository = make_repository(pushed_at=NOW - timedelta(days=900), archived=True)

    finding = finding_for(
        evaluate(context_for("README.md", repository=repository)), "maintenance.activity"
    )

    assert finding.status is Status.OK
    assert "archivé" in finding.message


def test_an_empty_repository_opens_an_activity_finding() -> None:
    repository = make_repository(pushed_at=None)

    finding = finding_for(evaluate(context_for(repository=repository)), "maintenance.activity")

    assert finding.status is Status.OPEN
    assert "vide" in finding.message


def test_the_most_recent_of_push_and_commit_dates_is_used() -> None:
    """``pushed_at`` couvre toute l'histoire, les commits la seule fenêtre relevée."""
    repository = make_repository(pushed_at=NOW - timedelta(days=400))
    context = RuleContext(repository=repository, markers={}, now=NOW)

    assert context.last_activity_at == repository.pushed_at


# ── Éditorial et mise à jour automatique (étape 44) ─────────────────────────


def test_a_detected_legal_notice_satisfies_its_rule() -> None:
    finding = finding_for(
        evaluate(context_for("README.md", content_signals={"legal_notice": True})),
        "editorial.legal_notice",
    )

    assert finding.status is Status.OK
    assert finding.severity is Severity.INFO


def test_a_missing_legal_notice_opens_a_medium_finding() -> None:
    finding = finding_for(evaluate(context_for("README.md")), "editorial.legal_notice")

    assert finding.status is Status.OPEN
    assert finding.severity is Severity.MEDIUM
    assert finding.recommendation


def test_a_detected_about_section_satisfies_its_rule() -> None:
    finding = finding_for(
        evaluate(context_for("README.md", content_signals={"about": True})), "editorial.about"
    )

    assert finding.status is Status.OK


def test_a_missing_about_section_opens_a_low_finding() -> None:
    finding = finding_for(evaluate(context_for("README.md")), "editorial.about")

    assert finding.status is Status.OPEN
    assert finding.severity is Severity.LOW


def test_a_detected_swinux_link_satisfies_its_rule() -> None:
    finding = finding_for(
        evaluate(context_for("README.md", content_signals={"swinux_link": True})),
        "editorial.swinux_link",
    )

    assert finding.status is Status.OK


def test_a_missing_swinux_link_opens_a_finding() -> None:
    finding = finding_for(evaluate(context_for("README.md")), "editorial.swinux_link")

    assert finding.status is Status.OPEN


def test_a_detected_auto_update_dependency_satisfies_its_rule() -> None:
    finding = finding_for(
        evaluate(context_for("README.md", content_signals={"auto_update": True})),
        "maintenance.auto_update",
    )

    assert finding.status is Status.OK


def test_a_missing_auto_update_dependency_opens_a_medium_finding() -> None:
    finding = finding_for(evaluate(context_for("README.md")), "maintenance.auto_update")

    assert finding.status is Status.OPEN
    assert finding.severity is Severity.MEDIUM


# ── Moteur ───────────────────────────────────────────────────────────────────


def test_every_rule_produces_exactly_one_finding() -> None:
    findings = evaluate(context_for("README.md"))

    assert len(findings) == len(RULES)
    assert {finding.rule for finding in findings} == {rule.id for rule in RULES}


def test_open_findings_keeps_only_what_is_missing() -> None:
    findings = evaluate(context_for("README.md"))
    opened = open_findings(findings)

    assert opened
    assert all(finding.status is Status.OPEN for finding in opened)
    assert "documentation.readme" not in {finding.rule for finding in opened}


def test_a_complete_repository_has_no_open_finding() -> None:
    findings = evaluate(
        context_for(
            "README.md",
            "LICENSE",
            "CHANGELOG.md",
            "CONTRIBUTING.md",
            "docs/index.md",
            "tests/test_app.py",
            ".github/workflows/ci.yml",
            ".github/dependabot.yml",
            "SECURITY.md",
            "Dockerfile",
            content_signals={
                "legal_notice": True,
                "about": True,
                "swinux_link": True,
                "auto_update": True,
            },
        )
    )

    assert open_findings(findings) == []


def test_the_engine_accepts_a_custom_catalog() -> None:
    """Ajouter une règle ne demande rien d'autre que de l'inscrire au catalogue."""
    rule = MarkerRule(
        id="documentation.editorconfig",
        category="documentation",
        label=".editorconfig",
        severity=Severity.LOW,
        marker="editorconfig",
        recommendation="Ajouter un .editorconfig.",
    )

    findings = evaluate(context_for(".editorconfig"), rules=[rule])

    assert len(findings) == 1
    assert findings[0].status is Status.OK


def test_a_rule_threshold_is_configurable() -> None:
    rule = InactivityRule(
        id="maintenance.strict",
        category="maintenance",
        label="Activité",
        severity=Severity.HIGH,
        stale_after_days=1,
        recommendation="Reprendre le projet.",
    )

    finding = rule.evaluate(context_for("README.md"))

    assert finding.status is Status.OPEN
    assert finding.severity is Severity.HIGH


def test_a_finding_is_immutable() -> None:
    finding = evaluate(context_for("README.md"))[0]

    with pytest.raises(Exception, match="frozen|immutable"):
        finding.status = Status.OK


# ── Persistance ──────────────────────────────────────────────────────────────


@pytest.fixture
def database() -> Database:
    """Base en mémoire, schéma créé."""
    instance = Database.in_memory()
    instance.create_schema()
    return instance


def store_findings(database: Database, findings: list[Finding]) -> tuple[int, int]:
    """Enregistre un repository, un snapshot et ses constats."""
    repository = make_repository()
    with database.session() as session:
        row, _ = upsert_repository(session, repository)
        snapshot = add_snapshot(session, row.id, RepositorySnapshot(collected_at=NOW))
        save_findings(session, row.id, snapshot.id, findings)
        return row.id, snapshot.id


def test_findings_are_persisted_and_read_back(database: Database) -> None:
    findings = evaluate(context_for("README.md"))

    repository_id, snapshot_id = store_findings(database, findings)

    with database.session() as session:
        stored = findings_for_snapshot(session, snapshot_id)
        latest = latest_findings(session, repository_id)

    assert len(stored) == len(findings)
    assert [row.rule for row in stored] == sorted(row.rule for row in stored)
    assert {row.rule for row in latest} == {finding.rule for finding in findings}


def test_a_stored_finding_keeps_its_explanation(database: Database) -> None:
    findings = evaluate(context_for("README.md"))

    _, snapshot_id = store_findings(database, findings)

    with database.session() as session:
        changelog = next(
            row for row in findings_for_snapshot(session, snapshot_id) if "changelog" in row.rule
        )

    assert changelog.status == Status.OPEN
    assert changelog.severity == Severity.MEDIUM
    assert changelog.message
    assert changelog.recommendation


def test_the_same_rule_cannot_be_recorded_twice_for_one_snapshot(database: Database) -> None:
    findings = evaluate(context_for("README.md"))
    repository_id, snapshot_id = store_findings(database, findings)

    with (
        pytest.raises(Exception, match="uq_finding_per_snapshot|UNIQUE"),
        database.session() as (session),
    ):
        session.add(
            FindingRow(
                repository_id=repository_id,
                snapshot_id=snapshot_id,
                category="documentation",
                rule="documentation.readme",
                severity="high",
                status="open",
                message="doublon",
            )
        )


def test_findings_are_empty_for_a_repository_without_snapshot(database: Database) -> None:
    with database.session() as session:
        row, _ = upsert_repository(session, make_repository())
        assert latest_findings(session, row.id) == []
