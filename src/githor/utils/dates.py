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


def parse_datetime(value: str | None) -> datetime | None:
    """Convertit une date ISO 8601 renvoyée par GitHub en datetime UTC.

    GitHub émet des dates de la forme ``2026-08-01T10:30:00Z``. Certaines dates
    sont légitimement nulles — ``pushed_at`` d'un dépôt vide, par exemple.

    Args:
        value: chaîne ISO 8601, ou ``None``.

    Returns:
        La date en UTC, ou ``None`` si l'entrée est vide.

    Raises:
        ValueError: si la chaîne n'est pas une date ISO 8601 exploitable.
    """
    if value is None or not value.strip():
        return None

    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        # GitHub date toujours en UTC ; une date naïve est donc de l'UTC.
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def isoformat(moment: datetime | None) -> str:
    """Formate une date en ISO 8601 UTC, ou en chaîne vide si elle est absente.

    Les exports doivent produire une date lisible par une machine, et une case
    vide plutôt qu'un ``None`` textuel là où la donnée manque.
    """
    if moment is None:
        return ""
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")
