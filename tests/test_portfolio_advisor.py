"""Tests du conseiller de projets multi-dépôts (étape 30, §35 du cahier des charges).

Aucun réseau ici : le générateur est un faux objet injecté, comme pour
``analysis.advisor``. Ce module ne teste ni la base ni la CLI.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from githor.analysis.portfolio_advisor import (
    answer_question,
    build_question_prompt,
    summarize_repository,
)
from githor.exporters.dataset import CodeExport, Dataset, FindingExport, RepositoryExport
from githor.scoring import Score

MOMENT = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def export(**overrides: object) -> RepositoryExport:
    """Construit un RepositoryExport minimal, sans base de données."""
    defaults: dict[str, object] = {
        "full_name": "nouhailler/Architecturor",
        "name": "Architecturor",
        "owner": "nouhailler",
        "url": "https://github.com/nouhailler/Architecturor",
        "visibility": "public",
        "default_branch": "main",
        "pushed_at": MOMENT,
    }
    return RepositoryExport(**{**defaults, **overrides})  # type: ignore[arg-type]


def dataset_of(*repositories: RepositoryExport) -> Dataset:
    return Dataset(
        githor_version="0.5.0",
        generated_at=MOMENT,
        repository_count=len(repositories),
        repositories=tuple(repositories),
    )


@dataclass
class _FakeGenerator:
    """Générateur injecté, sans réseau."""

    response: str = "Réponse."
    prompts: list[str] = field(default_factory=list)
    formats: list[str | None] = field(default_factory=list)

    def generate(self, prompt: str, *, model: str, format: str | None = "json") -> str:  # noqa: A002
        self.prompts.append(prompt)
        self.formats.append(format)
        return self.response


# ── Résumé d'un dépôt ────────────────────────────────────────────────────────


def test_summary_cites_the_full_name_and_language() -> None:
    line = summarize_repository(export())

    assert "nouhailler/Architecturor" in line


def test_summary_cites_the_score_when_available() -> None:
    line = summarize_repository(export(score=Score(docs=80, tests=0, ci=100, overall=60)))

    assert "score global : 60" in line
    assert "tests=0" in line
    assert "security=—" in line  # jamais évalué : une valeur absente, pas un zéro


def test_summary_shows_no_score_for_a_never_scanned_repository() -> None:
    line = summarize_repository(export(score=None))

    assert "score global : —" in line


def test_summary_lists_open_findings() -> None:
    line = summarize_repository(
        export(
            findings=(
                FindingExport(
                    category="development",
                    rule="development.tests",
                    severity="high",
                    status="open",
                    message="tests/ absent.",
                ),
            )
        )
    )

    assert "development.tests" in line


def test_summary_says_no_open_finding_explicitly() -> None:
    line = summarize_repository(export(findings=()))

    assert "constats ouverts : aucun" in line


def test_summary_reports_local_tests_from_the_code_audit() -> None:
    line = summarize_repository(
        export(code=CodeExport(analysed_at=MOMENT, commit="a" * 40, branch="main", test_files=3))
    )

    assert "tests locaux : 3 fichier(s)" in line


def test_summary_says_unknown_without_any_audit() -> None:
    line = summarize_repository(export(code=None))

    assert "jamais audité" in line


def test_summary_flags_an_archived_repository() -> None:
    assert "archivé" in summarize_repository(export(archived=True))
    assert "archivé" not in summarize_repository(export(archived=False))


# ── Prompt ───────────────────────────────────────────────────────────────────


def test_the_prompt_contains_the_question_and_every_repository() -> None:
    dataset = dataset_of(export(full_name="a/un"), export(full_name="b/deux"))

    prompt = build_question_prompt("Quels projets n'ont pas de tests ?", dataset)

    assert "Quels projets n'ont pas de tests ?" in prompt
    assert "a/un" in prompt
    assert "b/deux" in prompt


def test_the_prompt_asks_to_never_invent_beyond_the_data() -> None:
    prompt = build_question_prompt("Une question.", dataset_of(export()))

    assert "invent" in prompt.lower()


# ── Génération ───────────────────────────────────────────────────────────────


def test_answer_question_returns_the_generated_text_verbatim() -> None:
    generator = _FakeGenerator(response="Astror est le mieux documenté.")

    answer = answer_question(
        generator,
        question="Quel projet est le mieux documenté ?",
        dataset=dataset_of(export()),
        model="llama3.1",
    )

    assert answer == "Astror est le mieux documenté."


def test_answer_question_asks_for_free_text_not_json() -> None:
    generator = _FakeGenerator()

    answer_question(
        generator, question="Une question.", dataset=dataset_of(export()), model="llama3.1"
    )

    assert generator.formats == [None]


def test_answer_question_transmits_the_question_in_the_prompt() -> None:
    generator = _FakeGenerator()

    answer_question(
        generator,
        question="Quels projets sont abandonnés ?",
        dataset=dataset_of(export()),
        model="llama3.1",
    )

    assert "Quels projets sont abandonnés ?" in generator.prompts[0]
