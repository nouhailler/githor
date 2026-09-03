"""Point d'entrée de la CLI Githor.

Ce module ne contient que le câblage de la ligne de commande : les options
globales, la mise en place du logging, l'affichage et la traduction des
exceptions en messages lisibles. La logique métier vit dans les autres couches.
"""

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table
from sqlalchemy.orm import Session

from githor import __version__
from githor.collectors.activity import collect_activity
from githor.collectors.issues import collect_issues
from githor.collectors.languages import collect_languages
from githor.collectors.releases import collect_releases
from githor.collectors.repositories import (
    RepositoryCollection,
    build_snapshot,
    collect_repositories,
    normalise_repository,
)
from githor.collectors.structure import collect_structure
from githor.config import Config, ScanConfig, load_config
from githor.errors import GithorError
from githor.exporters import ExportFormat, build_dataset, write_export
from githor.github.client import GitHubClient, RateLimit
from githor.github.errors import NotFoundError
from githor.github.repositories import get_repository
from githor.github.token import find_token, require_token
from githor.github.user import get_authenticated_login, get_authenticated_user
from githor.logging import get_logger, setup_logging
from githor.models.finding import SEVERITY_LABELS, SEVERITY_ORDER, Severity, Status
from githor.models.repository import Repository
from githor.rules.base import RuleContext
from githor.rules.catalog import CATEGORIES, CATEGORY_LABELS, rule_labels
from githor.rules.engine import evaluate, open_findings
from githor.storage.database import Database
from githor.storage.findings import latest_findings, save_findings
from githor.storage.repositories import (
    add_snapshot,
    count_snapshots,
    find_repository_by_name,
    latest_snapshot,
    list_stored_repositories,
    save_commits,
    save_files,
    save_issues,
    save_languages,
    save_releases,
    upsert_repository,
)
from githor.storage.tables import FindingRow, RepositoryRow

console = Console()
stderr_console = Console(stderr=True)
logger = get_logger("cli")


@dataclass
class CLIState:
    """Options globales, renseignées par le callback et lues par les commandes."""

    debug: bool = False
    config_path: Path | None = None


state = CLIState()

app = typer.Typer(
    name="githor",
    help="Githor — inventaire, métriques et audit local de vos repositories GitHub.",
    no_args_is_help=True,
    # Le callback doit pouvoir s'exécuter même sans sous-commande, sinon les
    # options globales (--debug) resteraient sans effet.
    invoke_without_command=True,
    add_completion=False,
    # Les tracebacks Rich de Typer sont désactivées : la gestion des erreurs est
    # explicite dans ``main`` et n'affiche une pile qu'en mode debug.
    pretty_exceptions_enable=False,
)

config_app = typer.Typer(
    help="Inspection de la configuration effective.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
app.add_typer(config_app, name="config")

auth_app = typer.Typer(
    help="Authentification GitHub.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
app.add_typer(auth_app, name="auth")

db_app = typer.Typer(
    help="Base de données locale.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
app.add_typer(db_app, name="db")


def _version_callback(value: bool) -> None:
    """Affiche la version puis interrompt l'exécution (option eager)."""
    if value:
        console.print(f"githor {__version__}", highlight=False)
        raise typer.Exit()


@app.callback()
def cli(
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Active les logs détaillés et les tracebacks complètes."),
    ] = False,
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            metavar="FICHIER",
            help="Fichier de configuration TOML à utiliser au lieu de la recherche par défaut.",
        ),
    ] = None,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Affiche la version de Githor et quitte.",
        ),
    ] = False,
) -> None:
    """Options communes à toutes les commandes."""
    state.debug = debug
    state.config_path = config
    setup_logging(debug=debug)
    logger.debug("Githor %s — mode debug actif", __version__)


def current_config() -> Config:
    """Charge la configuration en tenant compte de l'option globale ``--config``."""
    return load_config(state.config_path)


def open_database(config: Config) -> Database:
    """Ouvre la base déclarée par la configuration."""
    return Database(config.storage.database, echo=state.debug)


@db_app.command("init")
def db_init() -> None:
    """Crée la base SQLite et son schéma. L'opération est idempotente."""
    config = current_config()
    existed = config.storage.database.exists()

    with open_database(config) as database:
        database.create_schema()
        tables = database.table_names()

    verb = "vérifiée" if existed else "créée"
    console.print(f"Base {verb} : [bold]{config.storage.database}[/bold]", highlight=False)
    console.print(f"{len(tables)} table(s) : {', '.join(tables)}", highlight=False)


@config_app.command("show")
def config_show() -> None:
    """Affiche la configuration effective et l'état du token GitHub."""
    config = current_config()

    table = Table(show_header=True, header_style="bold")
    table.add_column("Clé")
    table.add_column("Valeur", overflow="fold")

    source = str(config.source) if config.source else "valeurs par défaut"
    table.add_row("source", source)
    table.add_row("github.api_url", config.github.api_url)
    table.add_row("scan.include_forks", str(config.scan.include_forks).lower())
    table.add_row("scan.include_archived", str(config.scan.include_archived).lower())
    table.add_row("scan.commit_history_days", str(config.scan.commit_history_days))
    table.add_row("storage.database", str(config.storage.database))
    table.add_row("export.directory", str(config.export.directory))

    table.add_row("github.use_gh_cli", str(config.github.use_gh_cli).lower())

    # Le jeton n'est jamais affiché : seule sa provenance est signalée.
    token = find_token(allow_gh_cli=config.github.use_gh_cli)
    table.add_row("jeton GitHub", token.description if token else "absent")

    console.print(table)


@auth_app.command("check")
def auth_check() -> None:
    """Vérifie le jeton, joint GitHub et affiche l'utilisateur et le quota.

    Aucune écriture n'est effectuée : la commande se contente de lire
    ``/user`` puis ``/rate_limit``.
    """
    config = current_config()
    token = require_token(allow_gh_cli=config.github.use_gh_cli)

    with GitHubClient(token.value, api_url=config.github.api_url) as client:
        login = get_authenticated_user(client).get("login", "?")
        quota = client.get_rate_limit()

    report = Table.grid(padding=(0, 2))
    report.add_column(style="bold")
    report.add_column()
    report.add_row("Jeton", token.description)
    report.add_row("Utilisateur", str(login))
    report.add_row("API", f"joignable ({config.github.api_url})")
    report.add_row("Quota", _quota_summary(quota))
    report.add_row("Statut", "[green]OK[/green]")

    console.print("[bold]Authentification GitHub[/bold]\n")
    console.print(report)


@app.command("scan")
def scan(
    repository: Annotated[
        str | None,
        typer.Argument(
            metavar="[REPOSITORY]",
            help="Nom d'un dépôt à scanner seul, par exemple Architecturor "
            "ou nouhailler/Architecturor. Tous par défaut.",
        ),
    ] = None,
    include_forks: Annotated[
        bool,
        typer.Option("--include-forks", help="Inclut les forks, exclus par défaut."),
    ] = False,
    include_archived: Annotated[
        bool,
        typer.Option("--include-archived", help="Inclut les dépôts archivés, exclus par défaut."),
    ] = False,
) -> None:
    """Scanne les repositories et enregistre un snapshot de chacun.

    Chaque exécution **ajoute** un snapshot : les mesures précédentes sont
    conservées, afin de pouvoir suivre l'évolution des projets.
    """
    config = current_config()
    scope = _scope(config, include_forks=include_forks, include_archived=include_archived)
    token = require_token(allow_gh_cli=config.github.use_gh_cli)

    console.print("[bold]Githor — scan[/bold]\n")

    with GitHubClient(token.value, api_url=config.github.api_url) as client:
        if repository is None:
            with stderr_console.status("Récupération des repositories…"):
                collection = collect_repositories(client, scope)
        else:
            collection = RepositoryCollection([_fetch_one(client, repository)])

    if not collection.repositories:
        console.print("Aucun repository ne correspond au périmètre configuré.")
        _print_exclusions(collection)
        return

    console.print(f"Repositories à scanner : [bold]{len(collection.repositories)}[/bold]\n")

    with (
        GitHubClient(token.value, api_url=config.github.api_url) as client,
        open_database(config) as database,
    ):
        database.create_schema()
        totals = _persist(client, database, collection.repositories, scope)
        quota = client.rate_limit

    console.print(
        f"\n[bold]{len(collection.repositories)}[/bold] repository(s) scanné(s) : "
        f"{totals.created} nouveau(x), {totals.updated} mis à jour.",
        highlight=False,
    )
    console.print(
        f"{totals.snapshots} snapshot(s), {totals.languages} langage(s), "
        f"{totals.files} entrée(s) d'arborescence, {totals.commits} commit(s) ajouté(s).",
        highlight=False,
    )
    console.print(
        f"{totals.releases} release(s) et {totals.issues} issue(s) enregistrées.",
        highlight=False,
    )
    console.print(
        f"{totals.findings} constat(s) évalué(s), dont "
        f"[bold]{totals.findings_open}[/bold] ouvert(s).",
        highlight=False,
    )
    console.print(f"Base : {config.storage.database}", highlight=False)
    _print_exclusions(collection)

    if quota is not None and quota.is_low:
        console.print(f"[yellow]Quota GitHub bas : {quota.remaining} / {quota.limit}.[/yellow]")


def _scope(config: Config, *, include_forks: bool, include_archived: bool) -> ScanConfig:
    """Combine le périmètre configuré et les élargissements demandés en option."""
    return config.scan.model_copy(
        update={
            "include_forks": config.scan.include_forks or include_forks,
            "include_archived": config.scan.include_archived or include_archived,
        }
    )


def _fetch_one(client: GitHubClient, name: str) -> Repository:
    """Résout un nom de dépôt et récupère ses métadonnées.

    Un nom sans propriétaire est rattaché à l'utilisateur authentifié.
    """
    full_name = name if "/" in name else f"{get_authenticated_login(client)}/{name}"
    try:
        return normalise_repository(get_repository(client, full_name))
    except NotFoundError as exc:
        raise NotFoundError(f"Repository introuvable : {name}") from exc


@dataclass
class ScanTotals:
    """Décompte de ce qu'un scan a écrit."""

    created: int = 0
    updated: int = 0
    snapshots: int = 0
    languages: int = 0
    files: int = 0
    commits: int = 0
    findings: int = 0
    findings_open: int = 0
    releases: int = 0
    issues: int = 0


def _persist(
    client: GitHubClient,
    database: Database,
    repositories: Sequence[Repository],
    scope: ScanConfig,
) -> ScanTotals:
    """Collecte et enregistre, dépôt par dépôt, tout ce que le V0.1 mesure.

    Chaque dépôt est traité dans sa propre transaction : un dépôt en échec
    n'annule pas le travail déjà accompli sur les précédents.
    """
    totals = ScanTotals()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=stderr_console,
        transient=True,
    ) as progress:
        task = progress.add_task("Scan", total=len(repositories))

        for repository in repositories:
            progress.update(task, description=repository.full_name)

            languages = collect_languages(client, repository.full_name)
            structure = collect_structure(client, repository)
            activity = collect_activity(
                client, repository.full_name, days=scope.commit_history_days
            )
            releases = collect_releases(client, repository.full_name)
            issues = collect_issues(client, repository.full_name)
            findings = evaluate(
                RuleContext(repository=repository, markers=structure.markers, activity=activity)
            )
            opened = len(open_findings(findings))

            with database.session() as session:
                row, is_new = upsert_repository(session, repository)
                snapshot = add_snapshot(
                    session,
                    row.id,
                    build_snapshot(repository, open_prs=issues.open_pull_requests),
                )
                totals.languages += save_languages(session, snapshot.id, languages)
                totals.files += save_files(session, snapshot.id, structure.files)
                totals.commits += save_commits(session, row.id, activity.commits)
                totals.releases += save_releases(session, row.id, releases)
                totals.issues += save_issues(session, row.id, issues.issues)
                totals.findings += save_findings(session, row.id, snapshot.id, findings)
                total_snapshots = count_snapshots(session, row.id)

            totals.findings_open += opened
            totals.created += int(is_new)
            totals.updated += int(not is_new)
            totals.snapshots += 1

            marker = "[green]+[/green]" if is_new else "[green]✓[/green]"
            console.print(
                f"{marker} {repository.full_name} [dim](snapshot {total_snapshots} · "
                f"{structure.file_count} fichiers · {len(languages)} langages · "
                f"{activity.total} commits/{scope.commit_history_days}j · "
                f"{opened} constats)[/dim]",
                highlight=False,
            )
            progress.advance(task)

    return totals


@app.command("export")
def export(
    export_format: Annotated[
        ExportFormat,
        typer.Option(
            "--format",
            "-f",
            case_sensitive=False,
            help="Format de sortie.",
        ),
    ] = ExportFormat.JSON,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            metavar="RÉPERTOIRE",
            help="Répertoire de destination, au lieu de celui de la configuration.",
        ),
    ] = None,
) -> None:
    """Exporte le contenu de la base dans data/exports/.

    L'export décrit le dernier snapshot de chaque dépôt : il ne joint pas
    GitHub, et n'écrase jamais un export précédent — le nom du fichier est
    horodaté.
    """
    config = current_config()
    if not config.storage.database.exists():
        console.print(
            f"Aucune base à {config.storage.database} : lancez d'abord [bold]githor scan[/bold].",
            highlight=False,
        )
        return

    directory = output or config.export.directory

    with open_database(config) as database:
        database.create_schema()
        with database.session() as session:
            dataset = build_dataset(session)

    if not dataset.repositories:
        console.print("Aucun repository enregistré : lancez d'abord [bold]githor scan[/bold].")
        return

    path = write_export(dataset, export_format, directory)

    console.print(
        f"Export [bold]{export_format}[/bold] : {dataset.repository_count} repository(s), "
        f"{dataset.findings_open} constat(s) ouvert(s).",
        highlight=False,
    )
    console.print(f"Écrit dans : [bold]{path}[/bold]", highlight=False)


@app.command("findings")
def findings(
    repository: Annotated[
        str | None,
        typer.Argument(
            metavar="[REPOSITORY]",
            help="Dépôt dont on veut le détail. Sans argument, la synthèse de tous les dépôts.",
        ),
    ] = None,
) -> None:
    """Affiche les constats du dernier snapshot enregistré.

    La commande ne joint pas GitHub : elle relit la base produite par
    ``githor scan``.
    """
    config = current_config()
    if not config.storage.database.exists():
        console.print(
            f"Aucune base à {config.storage.database} : lancez d'abord [bold]githor scan[/bold].",
            highlight=False,
        )
        return

    with open_database(config) as database:
        database.create_schema()
        with database.session() as session:
            if repository is None:
                reports = [
                    _read_findings(session, row) for row in list_stored_repositories(session)
                ]
            else:
                row = find_repository_by_name(session, repository)
                if row is None:
                    console.print(
                        f"[red]Repository inconnu de la base :[/red] {repository}", highlight=False
                    )
                    console.print("[dim]Voir githor findings sans argument.[/dim]")
                    raise typer.Exit(code=1)
                reports = [_read_findings(session, row)]

    if not reports:
        console.print("Aucun repository enregistré : lancez d'abord [bold]githor scan[/bold].")
        return

    if repository is None:
        console.print(_findings_table(reports))
        console.print(
            f"\n[bold]{sum(report.opened for report in reports)}[/bold] constat(s) ouvert(s) "
            f"sur {len(reports)} repository(s).",
            highlight=False,
        )
        console.print("[dim]Détail d'un dépôt : githor findings NOM.[/dim]")
        return

    _print_findings_detail(reports[0])


@dataclass
class FindingsReport:
    """Constats du dernier snapshot d'un repository, prêts à l'affichage."""

    full_name: str
    collected_at: datetime | None
    findings: list[FindingRow]

    @property
    def opened(self) -> int:
        """Nombre de constats ouverts."""
        return sum(1 for finding in self.findings if finding.status == Status.OPEN)

    @property
    def satisfied(self) -> int:
        """Nombre de règles satisfaites."""
        return sum(1 for finding in self.findings if finding.status == Status.OK)

    def count(self, severity: Severity) -> int:
        """Nombre de constats ouverts d'une gravité donnée."""
        return sum(
            1
            for finding in self.findings
            if finding.status == Status.OPEN and finding.severity == severity
        )


def _read_findings(session: Session, row: RepositoryRow) -> FindingsReport:
    """Rassemble les constats du dernier snapshot d'un repository."""
    snapshot = latest_snapshot(session, row.id)
    return FindingsReport(
        full_name=row.full_name,
        collected_at=snapshot.collected_at if snapshot else None,
        findings=latest_findings(session, row.id),
    )


def _findings_table(reports: Sequence[FindingsReport]) -> Table:
    """Construit le tableau récapitulatif de tous les dépôts."""
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("Repository")
    table.add_column("Snapshot")
    table.add_column("✓", justify="right")
    table.add_column("✗", justify="right")
    for severity in (Severity.HIGH, Severity.MEDIUM, Severity.LOW):
        table.add_column(SEVERITY_LABELS[severity], justify="right")

    for report in reports:
        table.add_row(
            report.full_name,
            _format_date(report.collected_at),
            str(report.satisfied),
            str(report.opened),
            *(
                str(report.count(severity))
                for severity in (Severity.HIGH, Severity.MEDIUM, Severity.LOW)
            ),
        )
    return table


def _print_findings_detail(report: FindingsReport) -> None:
    """Affiche la synthèse « ce qui manque » puis les constats ouverts d'un dépôt."""
    console.print(f"[bold]{report.full_name}[/bold]", highlight=False)
    if not report.findings:
        console.print("Aucun constat enregistré : lancez [bold]githor scan[/bold] sur ce dépôt.")
        return

    console.print(f"[dim]Snapshot du {_format_date(report.collected_at)}[/dim]\n")

    labels = rule_labels()
    # Les constats reviennent triés par identifiant ; la synthèse, elle, suit
    # l'ordre du catalogue, qui va du plus attendu au plus accessoire.
    positions = {identifier: rank for rank, identifier in enumerate(labels)}
    by_category: dict[str, list[FindingRow]] = {category: [] for category in CATEGORIES}
    for finding in report.findings:
        by_category.setdefault(finding.category, []).append(finding)

    for category, findings in by_category.items():
        findings.sort(key=lambda finding: positions.get(finding.rule, len(positions)))
        if not findings:
            continue
        console.print(f"[bold]{CATEGORY_LABELS.get(category, category)}[/bold]")
        console.print("─" * 32, style="dim")
        grid = Table.grid(padding=(0, 2))
        grid.add_column(width=18)
        grid.add_column()
        for finding in findings:
            satisfied = finding.status == Status.OK
            grid.add_row(
                labels.get(finding.rule, finding.rule),
                "[green]✓[/green]" if satisfied else "[red]✗[/red]",
            )
        console.print(grid)
        console.print()

    opened = [finding for finding in report.findings if finding.status == Status.OPEN]
    if not opened:
        console.print("[green]Aucun constat ouvert.[/green]")
        return

    console.print(f"[bold]Constats ouverts ({len(opened)})[/bold]\n")
    for severity in SEVERITY_ORDER:
        group = [finding for finding in opened if finding.severity == severity]
        if not group:
            continue
        console.print(f"[bold]{SEVERITY_LABELS[severity]}[/bold]")
        for finding in group:
            console.print(f"  • {finding.message} [dim]({finding.rule})[/dim]", highlight=False)
            if finding.recommendation:
                console.print(f"    [dim]→ {finding.recommendation}[/dim]", highlight=False)
        console.print()


@app.command("repos")
def repos(
    include_forks: Annotated[
        bool,
        typer.Option("--include-forks", help="Inclut les forks, exclus par défaut."),
    ] = False,
    include_archived: Annotated[
        bool,
        typer.Option("--include-archived", help="Inclut les dépôts archivés, exclus par défaut."),
    ] = False,
) -> None:
    """Liste les repositories accessibles, après application du périmètre configuré."""
    config = current_config()
    scope = _scope(config, include_forks=include_forks, include_archived=include_archived)
    token = require_token(allow_gh_cli=config.github.use_gh_cli)

    with GitHubClient(token.value, api_url=config.github.api_url) as client:
        with stderr_console.status("Récupération des repositories…"):
            collection = collect_repositories(client, scope)
        quota = client.rate_limit

    if not collection.repositories:
        console.print("Aucun repository ne correspond au périmètre configuré.")
        _print_exclusions(collection)
        return

    console.print(_repositories_table(collection.repositories))
    console.print(
        f"\n[bold]{len(collection.repositories)}[/bold] repository(s) "
        f"sur {collection.total_seen} accessibles.",
        highlight=False,
    )
    _print_exclusions(collection)

    if quota is not None and quota.is_low:
        console.print(f"[yellow]Quota GitHub bas : {quota.remaining} / {quota.limit}.[/yellow]")


def _print_exclusions(collection: RepositoryCollection) -> None:
    """Détaille ce que le périmètre a écarté, pour que rien ne disparaisse en silence."""
    excluded: list[str] = []
    if collection.excluded_forks:
        excluded.append(f"{collection.excluded_forks} fork(s)")
    if collection.excluded_archived:
        excluded.append(f"{collection.excluded_archived} archivé(s)")

    if excluded:
        console.print(
            f"[dim]Exclus par le périmètre : {' et '.join(excluded)}. "
            "Voir --include-forks / --include-archived.[/dim]",
            highlight=False,
        )


def _repositories_table(repositories: Sequence[Repository]) -> Table:
    """Construit le tableau récapitulatif des repositories."""
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("Repository")
    table.add_column("Visibilité")
    table.add_column("Langage")
    table.add_column("★", justify="right")
    table.add_column("Issues", justify="right")
    table.add_column("Dernier push")

    for repository in repositories:
        table.add_row(
            repository.full_name,
            repository.visibility,
            repository.language or "—",
            str(repository.stars),
            str(repository.open_issues_count),
            _format_date(repository.pushed_at),
        )
    return table


def _format_date(moment: datetime | None) -> str:
    """Formate une date pour l'affichage, ou un tiret si elle est absente."""
    return moment.strftime("%Y-%m-%d") if moment else "—"


def _quota_summary(quota: RateLimit) -> str:
    """Formate le quota et, s'il est bas, l'échéance de sa réinitialisation."""
    summary = f"{quota.remaining} / {quota.limit}"
    if quota.is_low:
        minutes = quota.seconds_until_reset / 60
        return f"[yellow]{summary}[/yellow] — réinitialisation dans {minutes:.0f} min"
    return summary


def main() -> int:
    """Lance la CLI et traduit les erreurs en messages lisibles.

    Returns:
        Le code de sortie du processus.
    """
    try:
        app()
    except GithorError as exc:
        if state.debug:
            raise
        stderr_console.print(f"[red]Erreur :[/red] {exc}")
        return 1
    except KeyboardInterrupt:
        stderr_console.print("[yellow]Interrompu.[/yellow]")
        return 130
    except Exception as exc:  # noqa: BLE001 — dernier rempart avant l'utilisateur
        if state.debug:
            raise
        stderr_console.print(f"[red]Erreur inattendue :[/red] {exc}")
        stderr_console.print("[dim]Relancez avec --debug pour la trace complète.[/dim]")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
