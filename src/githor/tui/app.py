"""Interface interactive (lecture seule) sur les dépôts enregistrés (V0.6).

Aucune donnée nouvelle : cette couche relit ce qu'``exporters``/``reports``
savent déjà rendre pour la CLI — ``build_dataset`` pour la liste, exactement
``build_report``/``render_report`` pour le détail. ``githor tui`` n'écrit
jamais rien, et ne connaît que ``config``, ``storage``, ``exporters`` et
``reports`` — jamais ``cli.py``, qui l'invoque en sens inverse.
"""

from textual.app import App

from githor.config import Config
from githor.exporters.dataset import build_dataset
from githor.reports import build_report, render_report
from githor.storage.database import Database
from githor.storage.repositories import find_repository_by_name
from githor.tui.screens import EmptyScreen, RepositoryDetailScreen, RepositoryListScreen


class GithorApp(App[None]):
    """Point d'entrée de la TUI, lancé par ``githor tui``."""

    TITLE = "Githor"
    SUB_TITLE = "inventaire, métriques et audit local de repositories GitHub"

    def __init__(self, config: Config) -> None:
        super().__init__()
        self._database = Database(config.storage.database)

    def on_mount(self) -> None:
        """Ouvre la base, construit le jeu de données, et affiche le premier écran."""
        self._database.create_schema()
        with self._database.session() as session:
            dataset = build_dataset(session)

        if not dataset.repositories:
            self.push_screen(EmptyScreen())
            return

        self.push_screen(RepositoryListScreen(dataset.repositories, on_select=self.open_detail))

    def on_unmount(self) -> None:
        """Ferme la connexion à la base à la fermeture de l'application."""
        self._database.close()

    def open_detail(self, full_name: str) -> None:
        """Construit et affiche le rapport d'un dépôt, comme ``githor report``."""
        with self._database.session() as session:
            row = find_repository_by_name(session, full_name)
            if row is None:  # pragma: no cover — le nom vient de la liste déjà affichée
                return
            markdown = render_report(build_report(session, row))
        self.push_screen(RepositoryDetailScreen(full_name, markdown))
