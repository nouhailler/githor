"""Catalogue des règles du V0.1.

Ajouter une règle consiste à ajouter une entrée ici : rien n'est codé en dur
dans la CLI, qui se contente d'évaluer le catalogue et d'afficher le résultat.

Les gravités traduisent une seule idée : ce dont l'absence empêche un tiers
d'utiliser le projet est ``high``, ce qui gêne la maintenance est ``medium``,
ce qui relève du confort est ``low``.
"""

from githor.models.finding import Severity
from githor.rules.base import InactivityRule, MarkerRule, Rule

STALE_AFTER_DAYS = 180
"""Seuil d'inactivité : six mois sans push valent un constat."""

CATEGORIES: tuple[str, ...] = (
    "documentation",
    "development",
    "infrastructure",
    "security",
    "maintenance",
)
"""Ordre d'affichage des catégories."""

CATEGORY_LABELS: dict[str, str] = {
    "documentation": "Documentation",
    "development": "Development",
    "infrastructure": "Infrastructure",
    "security": "Security",
    "maintenance": "Maintenance",
}

RULES: tuple[Rule, ...] = (
    MarkerRule(
        id="documentation.readme",
        category="documentation",
        label="README",
        severity=Severity.HIGH,
        marker="readme",
        recommendation="Ajouter un README décrivant le projet, son installation et son usage.",
    ),
    MarkerRule(
        id="documentation.license",
        category="documentation",
        label="LICENSE",
        severity=Severity.MEDIUM,
        marker="license",
        recommendation="Ajouter un fichier LICENSE : sans licence explicite, "
        "le code n'est pas réutilisable.",
    ),
    MarkerRule(
        id="documentation.changelog",
        category="documentation",
        label="CHANGELOG",
        severity=Severity.MEDIUM,
        marker="changelog",
        recommendation="Ajouter un CHANGELOG.md retraçant les évolutions du projet.",
    ),
    MarkerRule(
        id="documentation.contributing",
        category="documentation",
        label="CONTRIBUTING",
        severity=Severity.LOW,
        marker="contributing",
        recommendation="Ajouter un CONTRIBUTING.md expliquant comment contribuer.",
    ),
    MarkerRule(
        id="documentation.docs",
        category="documentation",
        label="docs/",
        severity=Severity.LOW,
        marker="docs",
        recommendation="Ajouter un répertoire docs/ pour la documentation détaillée.",
    ),
    MarkerRule(
        id="development.tests",
        category="development",
        label="tests/",
        severity=Severity.HIGH,
        marker="tests",
        recommendation="Ajouter un répertoire de tests : sans tests, "
        "aucune évolution n'est vérifiable.",
    ),
    MarkerRule(
        id="development.github_actions",
        category="development",
        label="GitHub Actions",
        severity=Severity.MEDIUM,
        marker="github_workflows",
        recommendation="Ajouter un workflow dans .github/workflows/ pour automatiser "
        "les vérifications.",
    ),
    MarkerRule(
        id="infrastructure.docker",
        category="infrastructure",
        label="Docker",
        severity=Severity.LOW,
        marker="dockerfile",
        recommendation="Ajouter un Dockerfile si le projet doit pouvoir être conteneurisé.",
    ),
    MarkerRule(
        id="security.dependabot",
        category="security",
        label="Dependabot",
        severity=Severity.LOW,
        marker="dependabot",
        recommendation="Ajouter .github/dependabot.yml pour suivre les mises à jour "
        "de dépendances.",
    ),
    MarkerRule(
        id="security.policy",
        category="security",
        label="Politique de sécurité (SECURITY.md)",
        severity=Severity.MEDIUM,
        marker="security",
        recommendation="Ajouter un SECURITY.md décrivant comment signaler une vulnérabilité.",
    ),
    InactivityRule(
        id="maintenance.activity",
        category="maintenance",
        label="Activité",
        severity=Severity.MEDIUM,
        stale_after_days=STALE_AFTER_DAYS,
        recommendation="Reprendre le projet ou l'archiver, pour que son état reflète l'intention.",
    ),
)
"""Règles appliquées à chaque snapshot, dans l'ordre d'affichage."""


def rules_by_category() -> dict[str, list[Rule]]:
    """Regroupe le catalogue par catégorie, dans l'ordre de :data:`CATEGORIES`."""
    grouped: dict[str, list[Rule]] = {category: [] for category in CATEGORIES}
    for rule in RULES:
        grouped.setdefault(rule.category, []).append(rule)
    return grouped


def rule_labels() -> dict[str, str]:
    """Associe chaque identifiant de règle à son libellé lisible."""
    return {rule.id: rule.label for rule in RULES}
