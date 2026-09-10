"""Interface web locale, en lecture seule, sur les dépôts enregistrés (0.7.0).

Une quatrième vue sur les mêmes données que la CLI, les exports et la TUI :
aucune requête ni logique de rendu propre à cette couche au-delà de la mise
en forme HTML — la page de détail construit exactement le même ``Report``
que ``githor report``/``githor tui``. Ce module ne connaît que ``config``,
``storage``, ``exporters`` et ``reports`` — jamais ``cli.py``, qui l'invoque
en sens inverse.
"""

from flask import Flask, abort, render_template

from githor.config import Config
from githor.exporters.dataset import RepositoryExport, build_dataset
from githor.reports import build_report
from githor.storage.database import Database
from githor.storage.repositories import find_repository_by_name
from githor.web.rendering import (
    checks_by_category,
    moment,
    open_findings_by_severity,
    score_badge,
    score_cell,
)


def create_app(config: Config) -> Flask:
    """Construit l'application, prête à être lancée par ``githor web``.

    La base est ouverte une fois ; chaque requête n'ouvre qu'une session
    courte, sur le même principe que le reste des commandes locales.
    """
    app = Flask(__name__)
    database = Database(config.storage.database)
    database.create_schema()

    app.jinja_env.filters["score_cell"] = score_cell
    app.jinja_env.filters["score_badge"] = score_badge
    app.jinja_env.filters["moment"] = moment

    def _rank(export: RepositoryExport) -> int:
        overall = export.score.overall if export.score else None
        return -1 if overall is None else overall

    @app.get("/")
    def index() -> str:
        """Liste les dépôts, triés du meilleur score au plus faible."""
        with database.session() as session:
            dataset = build_dataset(session)
        repositories = sorted(dataset.repositories, key=_rank, reverse=True)
        return render_template("list.html", repositories=repositories)

    @app.get("/repos/<path:full_name>")
    def detail(full_name: str) -> str:
        """Détail d'un dépôt — exactement ce que produit ``githor report``."""
        with database.session() as session:
            row = find_repository_by_name(session, full_name)
            if row is None:
                abort(404)
            report = build_report(session, row)

        return render_template(
            "detail.html",
            report=report,
            repository=report.repository,
            checks=checks_by_category(report.repository),
            open_findings=open_findings_by_severity(report.repository),
        )

    return app
