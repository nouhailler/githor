"""Tests du conseiller IA (étape 25) : priorisation, prompt, génération.

Aucun réseau ici : le générateur est un faux objet injecté, sur le modèle des
tests de ``githor.scoring``. Ce module ne teste ni la base ni la CLI.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from githor.analysis.advisor import build_prompt, generate_advice, rank_open_findings
from githor.models.advice import Advice


@dataclass
class _Finding:
    rule: str
    severity: str
    status: str
    message: str


@dataclass
class _Repository:
    full_name: str
    description: str | None = None


@dataclass
class _FakeGenerator:
    """Générateur injecté, sans réseau."""

    response: str = "[]"
    prompts: list[str] = field(default_factory=list)

    def generate(self, prompt: str, *, model: str) -> str:
        self.prompts.append(prompt)
        return self.response


def ok(rule: str, severity: str = "low") -> _Finding:
    return _Finding(rule=rule, severity=severity, status="ok", message=f"{rule} présent.")


def opened(rule: str, severity: str, message: str | None = None) -> _Finding:
    return _Finding(
        rule=rule, severity=severity, status="open", message=message or f"{rule} absent."
    )


REPOSITORY = _Repository(full_name="nouhailler/Architecturor", description="Un projet.")


# ── Priorisation ─────────────────────────────────────────────────────────────


def test_only_open_findings_are_ranked() -> None:
    findings = [ok("documentation.readme"), opened("development.tests", "high")]

    ranked = rank_open_findings(findings)

    assert [finding.rule for finding in ranked] == ["development.tests"]


def test_ranking_follows_severity_then_rule() -> None:
    findings = [
        opened("documentation.license", "medium"),
        opened("development.tests", "high"),
        opened("infrastructure.docker", "low"),
        opened("documentation.changelog", "medium"),
    ]

    ranked = rank_open_findings(findings)

    assert [finding.rule for finding in ranked] == [
        "development.tests",
        "documentation.changelog",
        "documentation.license",
        "infrastructure.docker",
    ]


def test_an_unknown_severity_is_ranked_last() -> None:
    findings = [opened("x.y", "inconnue"), opened("development.tests", "high")]

    ranked = rank_open_findings(findings)

    assert [finding.rule for finding in ranked] == ["development.tests", "x.y"]


# ── Prompt ───────────────────────────────────────────────────────────────────


def test_the_prompt_cites_every_ranked_finding() -> None:
    findings = [opened("development.tests", "high", "tests/ absent.")]

    prompt = build_prompt(REPOSITORY, findings, score=None, code=None)

    assert "development.tests" in prompt
    assert "tests/ absent." in prompt
    assert "nouhailler/Architecturor" in prompt


def test_the_prompt_asks_for_exactly_as_many_items_as_findings() -> None:
    findings = [opened("a.b", "high"), opened("c.d", "medium")]

    prompt = build_prompt(REPOSITORY, findings, score=None, code=None)

    assert "2 élément(s)" in prompt


def test_the_prompt_never_invents_a_finding_not_given() -> None:
    findings = [opened("development.tests", "high")]

    prompt = build_prompt(REPOSITORY, findings, score=None, code=None)

    assert "documentation.readme" not in prompt


# ── Génération ───────────────────────────────────────────────────────────────


def test_no_open_finding_yields_no_advice() -> None:
    generator = _FakeGenerator()

    advice = generate_advice(
        generator, repository=REPOSITORY, findings=[ok("documentation.readme")], model="llama3.1"
    )

    assert advice is None
    assert generator.prompts == []  # inutile d'appeler Ollama sans rien à recommander


def test_a_well_formed_response_maps_one_item_per_finding() -> None:
    findings = [opened("development.tests", "high"), opened("documentation.license", "medium")]
    generator = _FakeGenerator(
        response=json.dumps(
            [
                {"title": "Ajouter des tests", "recommendation": "Créer tests/."},
                {"title": "Ajouter une licence", "recommendation": "Ajouter LICENSE."},
            ]
        )
    )

    advice = generate_advice(generator, repository=REPOSITORY, findings=findings, model="llama3.1")

    assert advice is not None
    assert not advice.degraded
    assert [item.source_rule for item in advice.items] == [
        "development.tests",
        "documentation.license",
    ]
    assert advice.items[0].title == "Ajouter des tests"
    assert advice.model == "llama3.1"


def test_invalid_json_degrades_to_a_single_raw_item() -> None:
    findings = [opened("development.tests", "high")]
    generator = _FakeGenerator(response="ceci n'est pas du json")

    advice = generate_advice(generator, repository=REPOSITORY, findings=findings, model="llama3.1")

    assert advice is not None
    assert advice.degraded
    assert len(advice.items) == 1
    assert advice.items[0].source_rule is None
    assert advice.items[0].recommendation == "ceci n'est pas du json"


def test_a_mismatched_item_count_degrades(caplog: pytest.LogCaptureFixture) -> None:
    findings = [opened("development.tests", "high"), opened("documentation.license", "medium")]
    generator = _FakeGenerator(response=json.dumps([{"title": "Un seul", "recommendation": "…"}]))

    advice = generate_advice(generator, repository=REPOSITORY, findings=findings, model="llama3.1")

    assert advice is not None
    assert advice.degraded


def test_generated_at_is_injectable() -> None:
    moment = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    generator = _FakeGenerator(response="[]")
    findings = [opened("development.tests", "high")]

    advice = generate_advice(
        generator,
        repository=REPOSITORY,
        findings=findings,
        model="llama3.1",
        generated_at=moment,
    )

    assert isinstance(advice, Advice)
    assert advice.generated_at == moment
