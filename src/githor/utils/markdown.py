"""Fragments de rendu Markdown partagés par les exports et les rapports.

Un export et un rapport écrivent le même dialecte : mêmes dates à la minute,
mêmes cases de tableau neutralisées, mêmes libellés de gravité. Rassembler ces
gestes ici évite qu'ils divergent, et qu'un tableau se casse d'un côté
seulement.
"""

from datetime import UTC, datetime

from githor.models.finding import SEVERITY_LABELS, Severity

ABSENT = "—"
"""Ce qu'affiche une valeur manquante, pour qu'une case ne reste jamais vide."""


def format_moment(moment: datetime | None) -> str:
    """Formate une date pour un lecteur humain, à la minute près et en UTC."""
    if moment is None:
        return ""
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def severity_label(severity: str) -> str:
    """Traduit une gravité en libellé lisible, sans supposer qu'elle soit connue.

    Une gravité inconnue — base écrite par une version ultérieure — est rendue
    telle quelle plutôt que de faire échouer le rendu.
    """
    if severity in Severity:
        return SEVERITY_LABELS[Severity(severity)]
    return severity


def escape_cell(text: str | None) -> str:
    """Neutralise ce qui casserait un tableau Markdown : barres verticales et retours ligne."""
    if not text:
        return ""
    return text.replace("|", "\\|").replace("\n", " ").strip()
