"""Modèle normalisé d'un constat produit par une règle.

Un finding répond toujours à deux questions : **quel fait** a été constaté, et
**quelle règle** l'a constaté. Aucune interprétation, aucune IA : le V0.1 est
entièrement déterministe, et un constat doit pouvoir être vérifié à la main.

Les règles satisfaites produisent elles aussi un finding, de statut ``ok``.
Conserver les deux issues permet de reconstituer la synthèse « ce qui est
présent / ce qui manque » à partir de la seule base, et de savoir plus tard à
quelle date un projet a gagné son CHANGELOG.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Severity(StrEnum):
    """Gravité d'un constat ouvert.

    ``INFO`` est réservé aux règles satisfaites : un fait constaté sans manque.
    """

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Status(StrEnum):
    """État d'un constat."""

    OK = "ok"
    """La règle est satisfaite : rien à signaler."""

    OPEN = "open"
    """La règle n'est pas satisfaite : le constat reste ouvert."""


SEVERITY_ORDER: tuple[Severity, ...] = (
    Severity.HIGH,
    Severity.MEDIUM,
    Severity.LOW,
    Severity.INFO,
)
"""Ordre d'affichage : du plus grave au moins grave."""

SEVERITY_LABELS: dict[Severity, str] = {
    Severity.HIGH: "Élevée",
    Severity.MEDIUM: "Moyenne",
    Severity.LOW: "Faible",
    Severity.INFO: "Information",
}
"""Libellés français, pour l'affichage uniquement."""


class Finding(BaseModel):
    """Constat produit par une règle sur un repository, à la date du snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: str
    """Famille de la règle : ``documentation``, ``development``, etc."""

    rule: str
    """Identifiant stable de la règle, par exemple ``documentation.changelog``."""

    severity: Severity
    status: Status

    message: str
    """Fait constaté, formulé de façon vérifiable et citant le chemin trouvé."""

    recommendation: str | None = None
    """Action suggérée ; nulle quand la règle est satisfaite."""

    @property
    def is_open(self) -> bool:
        """Vrai si le constat signale un manque."""
        return self.status is Status.OPEN
