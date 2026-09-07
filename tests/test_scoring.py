"""Tests du score dérivé des findings (étape 20).

Le score ne stocke rien : il se recalcule depuis des findings déjà persistés
ou exportés. Les tests portent donc sur la fonction pure, sans base de données.
"""

from dataclasses import dataclass

from githor.scoring import Score, compute_score


@dataclass
class _Finding:
    """Double minimal, respectant la forme attendue par :func:`compute_score`."""

    rule: str
    status: str


def ok(rule: str) -> _Finding:
    return _Finding(rule=rule, status="ok")


def open_(rule: str) -> _Finding:
    return _Finding(rule=rule, status="open")


def test_no_findings_yields_no_score() -> None:
    assert compute_score([]) is None


def test_a_fully_satisfied_repository_scores_one_hundred_everywhere() -> None:
    score = compute_score(
        [
            ok("documentation.readme"),
            ok("documentation.license"),
            ok("documentation.changelog"),
            ok("documentation.contributing"),
            ok("documentation.docs"),
            ok("development.tests"),
            ok("development.github_actions"),
            ok("security.dependabot"),
            ok("security.policy"),
            ok("maintenance.activity"),
        ]
    )

    assert score == Score(docs=100, tests=100, ci=100, security=100, overall=100)


def test_a_fully_open_repository_scores_zero_everywhere() -> None:
    score = compute_score(
        [
            open_("documentation.readme"),
            open_("development.tests"),
            open_("development.github_actions"),
            open_("security.dependabot"),
            open_("security.policy"),
        ]
    )

    assert score is not None
    assert score.docs == 0
    assert score.tests == 0
    assert score.ci == 0
    assert score.security == 0
    assert score.overall == 0


def test_a_partial_group_is_rounded_to_the_nearest_percent() -> None:
    score = compute_score(
        [
            ok("documentation.readme"),
            open_("documentation.license"),
            open_("documentation.changelog"),
        ]
    )

    assert score is not None
    assert score.docs == 33
    assert score.tests is None
    assert score.overall == 33


def test_a_group_absent_from_the_findings_scores_none_not_zero() -> None:
    """Une valeur absente vaut mieux qu'un chiffre faux."""
    score = compute_score([ok("documentation.readme")])

    assert score is not None
    assert score.docs == 100
    assert score.tests is None
    assert score.ci is None
    assert score.security is None
    assert score.overall == 100


def test_overall_covers_every_finding_not_only_the_grouped_ones() -> None:
    """Une règle hors des groupes affichés (maintenance) compte quand même dans overall."""
    score = compute_score([ok("documentation.readme"), open_("maintenance.activity")])

    assert score is not None
    assert score.docs == 100
    assert score.overall == 50
