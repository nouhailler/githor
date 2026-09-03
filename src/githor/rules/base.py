"""Socle du moteur de règles : contexte, règle abstraite et règles génériques.

Une règle ne connaît ni GitHub ni SQLite : elle lit un :class:`RuleContext`
déjà collecté et rend un :class:`Verdict`. Ajouter une règle consiste donc à
écrire une classe — ou, le plus souvent, une simple entrée déclarative dans
:mod:`githor.rules.catalog`.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from githor.models.activity import Activity
from githor.models.finding import Finding, Severity, Status
from githor.models.repository import Repository
from githor.utils.dates import utc_now


@dataclass(frozen=True)
class RuleContext:
    """Tout ce qu'une règle a le droit de regarder.

    Le contexte est constitué une fois par repository, à partir de ce que les
    collectors ont déjà rapporté : aucune règle ne déclenche d'appel réseau.
    """

    repository: Repository
    markers: Mapping[str, str | None] = field(default_factory=dict)
    """Marqueur -> chemin qui l'a satisfait, tel que produit par les collectors."""

    activity: Activity | None = None
    now: datetime = field(default_factory=utc_now)
    """Instant de référence, injectable pour rendre les règles testables."""

    def marker(self, name: str) -> str | None:
        """Chemin ayant satisfait un marqueur, ou ``None`` s'il est absent."""
        return self.markers.get(name)

    @property
    def last_activity_at(self) -> datetime | None:
        """Date d'activité la plus récente connue, ou ``None`` pour un dépôt vide.

        ``pushed_at`` couvre toute l'histoire du dépôt là où les commits relevés
        se limitent à la fenêtre configurée : on retient la plus récente des deux.
        """
        candidates = [
            moment
            for moment in (
                self.repository.pushed_at,
                self.activity.last_commit_at if self.activity else None,
            )
            if moment is not None
        ]
        return max(candidates) if candidates else None


@dataclass(frozen=True)
class Verdict:
    """Ce qu'une règle a constaté."""

    satisfied: bool
    message: str
    """Fait constaté, cité de façon vérifiable."""

    recommendation: str | None = None
    """Action suggérée ; ignorée lorsque la règle est satisfaite."""


@dataclass(frozen=True)
class Rule(ABC):
    """Règle déterministe transformant un contexte en constat."""

    id: str
    """Identifiant stable, de la forme ``catégorie.nom``."""

    category: str
    label: str
    """Nom lisible, utilisé par la synthèse « ce qui manque »."""

    severity: Severity
    """Gravité appliquée lorsque la règle n'est pas satisfaite."""

    @abstractmethod
    def check(self, context: RuleContext) -> Verdict:
        """Applique la règle au contexte."""

    def evaluate(self, context: RuleContext) -> Finding:
        """Produit le constat correspondant au verdict de la règle."""
        verdict = self.check(context)
        return Finding(
            category=self.category,
            rule=self.id,
            severity=Severity.INFO if verdict.satisfied else self.severity,
            status=Status.OK if verdict.satisfied else Status.OPEN,
            message=verdict.message,
            recommendation=None if verdict.satisfied else verdict.recommendation,
        )


@dataclass(frozen=True)
class MarkerRule(Rule):
    """Règle satisfaite dès qu'un marqueur d'arborescence est présent.

    C'est la forme la plus courante : « le fichier attendu est-il là ? ». Le
    chemin trouvé est repris dans le message, afin que le constat cite toujours
    ce sur quoi il se fonde.
    """

    marker: str
    """Nom du marqueur, tel que défini dans :mod:`githor.collectors.structure`."""

    recommendation: str

    def check(self, context: RuleContext) -> Verdict:
        """Cherche le marqueur dans l'arborescence relevée."""
        found = context.marker(self.marker)
        if found is not None:
            return Verdict(True, f"{self.label} présent : {found}.")
        return Verdict(False, f"{self.label} absent.", self.recommendation)


@dataclass(frozen=True)
class InactivityRule(Rule):
    """Règle signalant un dépôt sans push depuis trop longtemps.

    Un dépôt archivé est considéré comme satisfait : son immobilité est voulue.
    """

    stale_after_days: int
    recommendation: str

    def check(self, context: RuleContext) -> Verdict:
        """Compare la dernière activité connue au seuil configuré."""
        if context.repository.archived:
            return Verdict(True, "Dépôt archivé : l'inactivité est attendue.")

        last = context.last_activity_at
        if last is None:
            return Verdict(
                False,
                "Aucune activité relevée : le dépôt paraît vide.",
                self.recommendation,
            )

        days = (context.now - last).days
        moment = last.strftime("%Y-%m-%d")
        if days >= self.stale_after_days:
            return Verdict(
                False,
                f"Aucun push depuis {days} jours (dernier le {moment}).",
                self.recommendation,
            )
        return Verdict(True, f"Dernier push il y a {days} jour(s), le {moment}.")
