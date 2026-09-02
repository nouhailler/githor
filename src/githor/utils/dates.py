"""Utilitaires de dates.

Toutes les dates manipulées par Githor sont conscientes du fuseau et exprimées
en UTC : GitHub renvoie de l'UTC, et les comparaisons d'historique doivent
rester indépendantes du fuseau de la machine.
"""

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Retourne l'instant présent en UTC."""
    return datetime.now(UTC)


def from_epoch(seconds: float) -> datetime:
    """Convertit un horodatage Unix en datetime UTC.

    Args:
        seconds: horodatage Unix, tel que renvoyé par l'en-tête X-RateLimit-Reset.
    """
    return datetime.fromtimestamp(seconds, tz=UTC)
