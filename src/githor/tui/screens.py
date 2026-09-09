"""Écrans de l'interface interactive (V0.6, lecture seule).

Aucun écran ne touche la base ni le disque : chacun reçoit déjà ce qu'il doit
montrer (un ``Dataset``, ou le Markdown d'un rapport) et ne fait qu'afficher.
C'est ``GithorApp`` (``app.py``) qui relit la base et compose les écrans.
"""

from collections.abc import Callable, Sequence

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Input, Markdown, Static

from githor.exporters.dataset import RepositoryExport

COLUMNS = ("Project", "Langage", "Docs", "Tests", "CI", "Security", "Score")


class EmptyScreen(Screen[None]):
    """Base présente mais sans aucun repository enregistré."""

    BINDINGS = [("q", "quit", "Quitter")]

    def compose(self) -> ComposeResult:
        """Assemble l'écran : un simple message, et le pied de raccourcis."""
        yield Header()
        yield Static("Aucun repository enregistré : lancez [bold]githor scan[/bold].")
        yield Footer()


class RepositoryListScreen(Screen[None]):
    """Liste des dépôts, triée par score — le même contenu que ``githor compare``."""

    BINDINGS = [("q", "quit", "Quitter")]

    # Une bordure sur le filtre coûterait deux lignes à un tableau qui en a
    # besoin pour afficher des dizaines de dépôts sans défiler pour rien.
    CSS = """
    RepositoryListScreen Input {
        border: none;
        height: 1;
    }
    """

    def __init__(
        self, repositories: Sequence[RepositoryExport], on_select: Callable[[str], None]
    ) -> None:
        """Prépare l'écran.

        Args:
            repositories: dépôts à lister, tels que renvoyés par ``build_dataset``.
            on_select: appelé avec le nom complet du dépôt choisi.
        """
        super().__init__()
        self._repositories = repositories
        self._on_select = on_select

    def compose(self) -> ComposeResult:
        """Assemble l'écran : le filtre, le tableau, et le pied de raccourcis."""
        yield Header()
        yield Input(placeholder="Filtrer par nom…", id="filter")
        yield DataTable(id="repositories")
        yield Footer()

    def on_mount(self) -> None:
        """Prépare les colonnes du tableau et le peuple une première fois."""
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.add_columns(*COLUMNS)
        self._populate(self._repositories)
        table.focus()

    def _populate(self, repositories: Sequence[RepositoryExport]) -> None:
        table = self.query_one(DataTable)
        table.clear()
        for export in sorted(repositories, key=_rank, reverse=True):
            score = export.score
            language = export.snapshot.primary_language if export.snapshot else None
            table.add_row(
                export.full_name,
                language or "—",
                _cell(score.docs if score else None),
                _cell(score.tests if score else None),
                _cell(score.ci if score else None),
                _cell(score.security if score else None),
                _cell(score.overall if score else None),
                key=export.full_name,
            )

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filtre la liste en direct, par sous-chaîne du nom complet."""
        needle = event.value.strip().lower()
        matches = [export for export in self._repositories if needle in export.full_name.lower()]
        self._populate(matches)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Ouvre le détail du dépôt correspondant à la ligne choisie."""
        if event.row_key.value is not None:
            self._on_select(str(event.row_key.value))


class RepositoryDetailScreen(Screen[None]):
    """Détail d'un dépôt — exactement le texte que produit ``githor report``."""

    BINDINGS = [
        ("escape", "pop_screen", "Retour"),
        ("backspace", "pop_screen", "Retour"),
    ]

    def __init__(self, full_name: str, report_markdown: str) -> None:
        super().__init__()
        self._full_name = full_name
        self._report_markdown = report_markdown

    def compose(self) -> ComposeResult:
        """Assemble l'écran : le rapport rendu en Markdown, et le pied de raccourcis."""
        yield Header()
        yield Markdown(self._report_markdown, id="report")
        yield Footer()

    def action_pop_screen(self) -> None:
        """Revient à l'écran précédent (la liste)."""
        self.app.pop_screen()


def _rank(export: RepositoryExport) -> int:
    """Trie du meilleur score au plus faible, un dépôt jamais scanné en dernier."""
    overall = export.score.overall if export.score else None
    return -1 if overall is None else overall


def _cell(value: int | None) -> str:
    """Une case vide plutôt qu'un zéro trompeur, pour un groupe jamais évalué."""
    return "—" if value is None else f"{value} %"
