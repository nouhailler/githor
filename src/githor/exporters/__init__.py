"""Exports du jeu de données : JSON, CSV et Markdown.

Un export est écrit dans un fichier **horodaté**, jamais par-dessus le
précédent : un export est une photographie, au même titre qu'un snapshot, et
deux exports successifs doivent pouvoir être comparés.
"""

from datetime import datetime
from enum import StrEnum
from pathlib import Path

from githor.errors import StorageError
from githor.exporters.csv_format import render_csv
from githor.exporters.dataset import Dataset, build_dataset, build_repository_export
from githor.exporters.json_format import render_json
from githor.exporters.markdown_format import render_markdown
from githor.logging import get_logger
from githor.utils.dates import utc_now

logger = get_logger("exporters")

FILE_PREFIX = "githor"


class ExportFormat(StrEnum):
    """Formats d'export proposés par le V0.1."""

    JSON = "json"
    CSV = "csv"
    MARKDOWN = "markdown"


EXTENSIONS: dict[ExportFormat, str] = {
    ExportFormat.JSON: ".json",
    ExportFormat.CSV: ".csv",
    ExportFormat.MARKDOWN: ".md",
}

RENDERERS = {
    ExportFormat.JSON: render_json,
    ExportFormat.CSV: render_csv,
    ExportFormat.MARKDOWN: render_markdown,
}


def render(dataset: Dataset, export_format: ExportFormat) -> str:
    """Rend le jeu de données dans le format demandé."""
    return RENDERERS[export_format](dataset)


def export_filename(export_format: ExportFormat, *, moment: datetime | None = None) -> str:
    """Compose un nom de fichier horodaté, trié chronologiquement par ordre alphabétique."""
    stamp = (moment or utc_now()).strftime("%Y%m%d-%H%M%S")
    return f"{FILE_PREFIX}-{stamp}{EXTENSIONS[export_format]}"


def write_export(
    dataset: Dataset,
    export_format: ExportFormat,
    directory: Path,
    *,
    moment: datetime | None = None,
) -> Path:
    """Écrit l'export dans le répertoire indiqué et retourne le fichier produit.

    Args:
        dataset: jeu de données à écrire.
        export_format: format demandé.
        directory: répertoire de destination, créé au besoin.
        moment: date servant à l'horodatage du nom ; maintenant par défaut.

    Raises:
        StorageError: si le répertoire ou le fichier est inaccessible.
    """
    path = directory / export_filename(export_format, moment=moment or dataset.generated_at)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(render(dataset, export_format), encoding="utf-8")
    except OSError as exc:
        raise StorageError(f"Export impossible vers {path} : {exc.strerror}") from exc

    logger.debug("Export %s écrit : %s", export_format, path)
    return path


__all__ = [
    "EXTENSIONS",
    "Dataset",
    "ExportFormat",
    "build_dataset",
    "build_repository_export",
    "export_filename",
    "render",
    "render_csv",
    "render_json",
    "render_markdown",
    "write_export",
]
