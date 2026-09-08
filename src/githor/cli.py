"""Point d'entrée de la CLI Githor.

Ce module ne contient que le câblage de la ligne de commande : les options
globales, la mise en place du logging, l'affichage et la traduction des
exceptions en messages lisibles. La logique métier vit dans les autres couches.
"""

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table
from sqlalchemy.orm import Session

from githor import __version__
from githor.analysis.advisor import generate_advice
from githor.analysis.audit import audit_checkout
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
from githor.config import AuditConfig, Config, ScanConfig, load_config
from githor.errors import GithorError
from githor.exporters import ExportFormat, build_dataset, render, write_export
from githor.exporters.dataset import Dataset, RepositoryExport, build_repository_export
from githor.github.client import GitHubClient, RateLimit
from githor.github.errors import NotFoundError
from githor.github.repositories import get_repository
from githor.github.token import find_token, require_token
from githor.github.user import get_authenticated_login, get_authenticated_user
from githor.logging import get_logger, setup_logging
from githor.models.advice import Advice
from githor.models.code import CodeAudit, DependencyScope, ImportKind
from githor.models.finding import SEVERITY_LABELS, SEVERITY_ORDER, Severity, Status
from githor.models.repository import Repository
from githor.ollama.client import OllamaClient
from githor.ollama.errors import OllamaError
from githor.reports import build_report, render_report, write_report
from githor.rules.base import RuleContext
from githor.rules.catalog import CATEGORIES, CATEGORY_LABELS, rule_labels
from githor.rules.engine import evaluate, open_findings
from githor.scoring import compute_score
from githor.storage.advice import count_advice_runs, save_advice
from githor.storage.code import audit_metrics, count_audits, latest_audit, save_audit
from githor.storage.database import Database
from githor.storage.findings import latest_findings, save_findings
from githor.storage.repositories import (
    add_snapshot,
    count_snapshots,
    find_repository_by_name,
    last_collected_at,
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
from githor.utils.dates import format_age, utc_now
from githor.vcs.git import Checkout, GitError, clone_url_for, ensure_checkout, git_version

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
    table.add_row("scan.snapshot_freshness_hours", str(config.scan.snapshot_freshness_hours))
    table.add_row("audit.workspace", str(config.audit.workspace))
    table.add_row("audit.clone_depth", str(config.audit.clone_depth))
    table.add_row("audit.git_timeout_seconds", str(config.audit.git_timeout_seconds))
    table.add_row("audit.max_file_bytes", str(config.audit.max_file_bytes))
    table.add_row("ollama.host", config.ollama.host)
    table.add_row("ollama.model", config.ollama.model)
    table.add_row("ollama.timeout_seconds", str(config.ollama.timeout_seconds))
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
    freshness: Annotated[
        int | None,
        typer.Option(
            "--freshness",
            metavar="HEURES",
            min=0,
            max=8760,
            help="Ignore les dépôts mesurés il y a moins de HEURES heures ; "
            "0 les remesure tous. Par défaut : scan.snapshot_freshness_hours.",
        ),
    ] = None,
) -> None:
    """Scanne les repositories et enregistre un snapshot de chacun.

    Chaque exécution **ajoute** un snapshot : les mesures précédentes sont
    conservées, afin de pouvoir suivre l'évolution des projets.

    Un dépôt déjà mesuré depuis moins de --freshness heures est ignoré : Githor
    n'interroge pas GitHub à son sujet et n'ajoute pas de snapshot.
    """
    config = current_config()
    scope = _scope(
        config,
        include_forks=include_forks,
        include_archived=include_archived,
        freshness=freshness,
    )
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
        f"\n[bold]{totals.scanned}[/bold] repository(s) scanné(s) : "
        f"{totals.created} nouveau(x), {totals.updated} mis à jour.",
        highlight=False,
    )
    if totals.skipped:
        console.print(
            f"{totals.skipped} repository(s) ignoré(s) : mesurés il y a moins de "
            f"{scope.snapshot_freshness_hours} h.",
            highlight=False,
        )
    # Trois lignes de zéros n'apprendraient rien quand tout a été jugé frais.
    if totals.scanned:
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


def _scope(
    config: Config, *, include_forks: bool, include_archived: bool, freshness: int | None = None
) -> ScanConfig:
    """Combine le périmètre configuré et ce que la ligne de commande en dit.

    Les élargissements de périmètre s'**ajoutent** à la configuration : une
    option ne peut que montrer davantage de dépôts. La fraîcheur, elle,
    **remplace** la valeur configurée, afin que ``--freshness 0`` puisse forcer
    un scan complet malgré une configuration plus permissive.
    """
    update: dict[str, object] = {
        "include_forks": config.scan.include_forks or include_forks,
        "include_archived": config.scan.include_archived or include_archived,
    }
    if freshness is not None:
        update["snapshot_freshness_hours"] = freshness
    return config.scan.model_copy(update=update)


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
    skipped: int = 0
    """Dépôts jugés assez frais pour n'être ni interrogés ni remesurés."""

    snapshots: int = 0
    languages: int = 0
    files: int = 0
    commits: int = 0
    findings: int = 0
    findings_open: int = 0
    releases: int = 0
    issues: int = 0

    @property
    def scanned(self) -> int:
        """Dépôts réellement mesurés, les ignorés mis à part."""
        return self.created + self.updated


def _fresh_age(last_collected: datetime | None, *, hours: int, now: datetime) -> timedelta | None:
    """Âge du dernier snapshot s'il dispense d'en reprendre un.

    Args:
        last_collected: date du dernier snapshot, ``None`` si le dépôt n'a
            jamais été mesuré.
        hours: durée de fraîcheur configurée ; ``0`` ne dispense de rien.
        now: instant de référence.

    Returns:
        L'âge de la mesure si elle est encore fraîche, ``None`` s'il faut
        rescanner. Une date en avance sur l'horloge est traitée comme fraîche :
        remesurer ne corrigerait pas une horloge qui dérive.
    """
    if hours <= 0 or last_collected is None:
        return None

    age = now - last_collected
    return age if age < timedelta(hours=hours) else None


def _persist(
    client: GitHubClient,
    database: Database,
    repositories: Sequence[Repository],
    scope: ScanConfig,
) -> ScanTotals:
    """Collecte et enregistre, dépôt par dépôt, tout ce que le V0.1 mesure.

    Chaque dépôt est traité dans sa propre transaction : un dépôt en échec
    n'annule pas le travail déjà accompli sur les précédents.

    Les dates des dernières mesures sont relues **d'un coup, avant la boucle** :
    un dépôt encore frais est écarté sans qu'aucune requête ne parte vers
    GitHub, ce qui est tout l'intérêt de la manœuvre.
    """
    totals = ScanTotals()
    now = utc_now()

    with database.session() as session:
        measured = last_collected_at(session, [item.github_id for item in repositories])

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

            age = _fresh_age(
                measured.get(repository.github_id),
                hours=scope.snapshot_freshness_hours,
                now=now,
            )
            if age is not None:
                totals.skipped += 1
                console.print(
                    f"[dim]· {repository.full_name} (mesuré il y a {format_age(age)} "
                    f"— ignoré)[/dim]",
                    highlight=False,
                )
                progress.advance(task)
                continue

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


@app.command("mirror")
def mirror(
    repository: Annotated[
        str | None,
        typer.Argument(
            metavar="REPOSITORY",
            help="Dépôt à cloner ou mettre à jour ; tous ceux de la base par défaut.",
        ),
    ] = None,
    offline: Annotated[
        bool,
        typer.Option(
            "--offline",
            help="N'interroge pas l'origine : se contente des copies déjà présentes.",
        ),
    ] = False,
) -> None:
    """Clone ou met à jour la copie locale des dépôts enregistrés.

    Le miroir est ce sur quoi porteront les analyses de code : il est cloné
    superficiellement, sur la seule branche par défaut, et n'est jamais modifié
    par Githor autrement qu'en le ramenant à l'état publié.

    La commande relit la **base** et n'appelle pas l'API GitHub : elle ne
    consomme donc aucun quota. Elle a en revanche besoin de « git » et, pour un
    dépôt privé, des identifiants que « git » utilise habituellement.
    """
    config = current_config()
    rows = _stored_repositories(config, repository)

    version = git_version(timeout=config.audit.git_timeout_seconds)
    console.print("[bold]Githor — miroir local[/bold]\n")
    console.print(f"[dim]{version} · {config.audit.workspace}[/dim]\n", highlight=False)

    created = updated = failed = 0

    for row in rows:
        try:
            checkout = _mirror_one(row, config.audit, fetch=not offline)
        except GitError as exc:
            failed += 1
            stderr_console.print(f"[red]✗[/red] {row.full_name} : {exc}", highlight=False)
            continue

        created += int(checkout.created)
        updated += int(not checkout.created)
        marker = "[green]+[/green]" if checkout.created else "[green]✓[/green]"
        console.print(
            f"{marker} {row.full_name} [dim]({checkout.branch} · {checkout.short_head} · "
            f"{checkout.path})[/dim]",
            highlight=False,
        )

    console.print(
        f"\n[bold]{created + updated}[/bold] miroir(s) à jour : "
        f"{created} cloné(s), {updated} relu(s).",
        highlight=False,
    )
    if offline:
        console.print("[dim]Mode hors ligne : aucune origine n'a été interrogée.[/dim]")
    if failed:
        console.print(f"[red]{failed} dépôt(s) en échec.[/red]", highlight=False)
        raise typer.Exit(code=1)


def _mirror_one(row: RepositoryRow, audit: AuditConfig, *, fetch: bool) -> Checkout:
    """Garantit le miroir d'un dépôt à partir de ce que la base sait de lui."""
    return ensure_checkout(
        url=clone_url_for(row.url),
        full_name=row.full_name,
        branch=row.default_branch,
        workspace=audit.workspace,
        depth=audit.clone_depth,
        timeout=audit.git_timeout_seconds,
        fetch=fetch,
    )


def _stored_repositories(config: Config, name: str | None) -> list[RepositoryRow]:
    """Retourne les dépôts visés dans la base : un seul, ou tous.

    Les commandes locales travaillent sur ce que le scan a déjà enregistré.
    Sans base, il n'y a rien à faire — et le dire vaut mieux que produire une
    liste vide.

    Raises:
        typer.Exit: base absente, base vide, ou nom inconnu.
    """
    if not config.storage.database.exists():
        console.print(
            f"Aucune base à {config.storage.database} : lancez d'abord [bold]githor scan[/bold].",
            highlight=False,
        )
        raise typer.Exit(code=1)

    with open_database(config) as database:
        database.create_schema()
        with database.session() as session:
            if name is None:
                rows = list_stored_repositories(session)
            else:
                found = find_repository_by_name(session, name)
                if found is None:
                    console.print(
                        f"[red]Repository inconnu de la base :[/red] {name}", highlight=False
                    )
                    console.print("[dim]Voir githor findings sans argument.[/dim]")
                    raise typer.Exit(code=1)
                rows = [found]

    if not rows:
        console.print("Aucun repository enregistré : lancez d'abord [bold]githor scan[/bold].")
        raise typer.Exit(code=1)

    return rows


@app.command("audit")
def audit(
    repository: Annotated[
        str | None,
        typer.Argument(
            metavar="REPOSITORY",
            help="Dépôt à analyser ; tous ceux de la base par défaut.",
        ),
    ] = None,
    offline: Annotated[
        bool,
        typer.Option(
            "--offline",
            help="N'interroge pas l'origine : analyse les miroirs déjà présents.",
        ),
    ] = False,
    save: Annotated[
        bool,
        typer.Option(
            "--save/--no-save",
            help="Enregistre l'analyse en base. Activé par défaut.",
        ),
    ] = True,
) -> None:
    """Analyse le code source des dépôts, localement.

    L'analyse porte sur le miroir local, qu'elle met à jour au passage. Elle
    compte les lignes de tous les langages reconnus, et n'établit structure,
    complexité et imports que pour Python — où ils viennent de l'AST de
    l'interpréteur, non d'une heuristique.

    Chaque exécution **ajoute** un audit : les analyses précédentes sont
    conservées, comme les snapshots, afin de pouvoir suivre l'évolution du code.

    Comme le reste des commandes locales, elle relit la base et n'appelle pas
    l'API GitHub : aucun quota n'est consommé.
    """
    config = current_config()
    rows = _stored_repositories(config, repository)

    console.print("[bold]Githor — audit du code[/bold]\n")
    detailed = len(rows) == 1
    results: list[tuple[str, CodeAudit]] = []
    failed = 0

    database = open_database(config)
    database.create_schema()

    for row in rows:
        try:
            checkout = _mirror_one(row, config.audit, fetch=not offline)
        except GitError as exc:
            failed += 1
            stderr_console.print(f"[red]✗[/red] {row.full_name} : {exc}", highlight=False)
            continue

        with stderr_console.status(f"Analyse de {row.full_name}…"):
            result = audit_checkout(
                checkout.path,
                commit=checkout.head,
                branch=checkout.branch,
                max_file_bytes=config.audit.max_file_bytes,
            )
        results.append((row.full_name, result))

        stored = 0
        if save:
            with database.session() as session:
                save_audit(session, row.id, result)
                stored = count_audits(session, row.id)

        if detailed:
            _print_audit_detail(row.full_name, result, audits=stored)
        else:
            console.print(
                f"[green]✓[/green] {row.full_name} [dim]({result.files_analysed} fichiers · "
                f"{result.lines.code} lignes de code)[/dim]",
                highlight=False,
            )

    database.close()

    if not detailed and results:
        console.print()
        console.print(_audit_table(results))

    console.print(
        f"\n[bold]{len(results)}[/bold] dépôt(s) analysé(s), "
        f"{sum(item.files_analysed for _, item in results)} fichier(s) lus.",
        highlight=False,
    )
    if save and results:
        console.print(f"Base : {config.storage.database}", highlight=False)
    elif results:
        console.print("[dim]--no-save : rien n'a été écrit en base.[/dim]")
    if failed:
        console.print(f"[red]{failed} dépôt(s) en échec.[/red]", highlight=False)
        raise typer.Exit(code=1)


def _print_audit_detail(full_name: str, result: CodeAudit, *, audits: int = 0) -> None:
    """Détaille l'analyse d'un seul dépôt.

    Args:
        full_name: nom complet du dépôt.
        result: analyse à rendre.
        audits: nombre d'audits conservés, ``0`` si rien n'a été enregistré.
    """
    kept = f" · audit {audits}" if audits else ""
    console.print(
        f"[bold]{full_name}[/bold] [dim]{result.branch} · {result.commit[:7]}{kept}[/dim]\n",
        highlight=False,
    )

    summary = Table(show_header=False, box=None, pad_edge=False)
    summary.add_column(style="dim")
    summary.add_column()

    lines = result.lines
    summary.add_row("Fichiers", _audit_files_summary(result))
    summary.add_row(
        "Lignes",
        f"{lines.total} ({lines.code} code, {lines.comment} commentaire, {lines.blank} vide)",
    )
    ratio = lines.comment_ratio
    summary.add_row("Part commentée", "—" if ratio is None else f"{ratio} %")
    summary.add_row(
        "Structure", f"{len(result.functions)} fonction(s), {result.class_count} classe(s)"
    )
    average = result.average_complexity
    summary.add_row(
        "Complexité",
        "—" if average is None else f"moyenne {average}, maximum {result.max_complexity}",
    )
    summary.add_row("Tests", _tests_summary(result))
    summary.add_row("Dépendances", _dependencies_summary(result))
    console.print(summary)

    if result.languages:
        console.print()
        console.print(_languages_table(result))

    complex_functions = result.most_complex()
    if complex_functions and result.max_complexity > 1:
        console.print()
        table = Table(title="Fonctions les plus complexes", title_justify="left")
        table.add_column("Complexité", justify="right")
        table.add_column("Fonction")
        table.add_column("Fichier", style="dim")
        placement = {
            function.name: module.path for module in result.modules for function in module.functions
        }
        for function in complex_functions:
            table.add_row(str(function.complexity), function.name, placement.get(function.name, ""))
        console.print(table)

    if result.dependencies:
        console.print()
        console.print(_dependencies_table(result))

    third_party = result.imports_of_kind(ImportKind.THIRD_PARTY)
    if third_party:
        console.print(f"\n[dim]Imports tierce partie :[/dim] {', '.join(third_party)}")

    undeclared = result.undeclared_imports
    if undeclared:
        # Piste à vérifier, jamais un manquement établi : un paquet s'installe
        # souvent sous un autre nom que celui sous lequel il s'importe.
        console.print(
            f"[dim]Importés sans être déclarés (à vérifier) :[/dim] {', '.join(undeclared)}"
        )

    errors = result.parse_errors
    if errors:
        console.print(f"\n[yellow]{len(errors)} fichier(s) non analysables :[/yellow]")
        for module in errors[:5]:
            console.print(f"  [dim]{module.path} — {module.parse_error}[/dim]", highlight=False)


def _audit_files_summary(result: CodeAudit) -> str:
    """Décrit ce qui a été lu et ce qui a été écarté, sans rien taire."""
    parts = [f"{result.files_analysed} analysé(s)"]
    if result.files_binary:
        parts.append(f"{result.files_binary} binaire(s)")
    if result.files_too_large:
        parts.append(f"{result.files_too_large} trop gros")
    return ", ".join(parts)


def _tests_summary(result: CodeAudit) -> str:
    """Décrit la suite de tests trouvée, ou dit qu'il n'y en a pas."""
    tests = result.tests
    if not tests.exists:
        return "[yellow]aucun fichier de test[/yellow]"

    parts = [f"{tests.files} fichier(s)"]
    if tests.functions:
        parts.append(f"{tests.functions} fonction(s)")
    if tests.frameworks:
        parts.append(", ".join(tests.frameworks))
    return " · ".join(parts)


def _dependencies_summary(result: CodeAudit) -> str:
    """Compte les dépendances déclarées par rôle."""
    if not result.dependencies:
        return "aucune déclarée"

    counts = [
        (label, len(result.dependencies_in_scope(scope)))
        for scope, label in (
            (DependencyScope.RUNTIME, "exécution"),
            (DependencyScope.DEVELOPMENT, "développement"),
            (DependencyScope.OPTIONAL, "optionnelle(s)"),
        )
    ]
    return ", ".join(f"{count} {label}" for label, count in counts if count)


def _dependencies_table(result: CodeAudit) -> Table:
    """Dépendances déclarées, avec le manifeste qui les déclare."""
    table = Table(title="Dépendances déclarées", title_justify="left")
    table.add_column("Paquet")
    table.add_column("Contrainte")
    table.add_column("Rôle")
    table.add_column("Écosystème", style="dim")
    table.add_column("Déclarée dans", style="dim")

    scopes = {
        DependencyScope.RUNTIME: "exécution",
        DependencyScope.DEVELOPMENT: "développement",
        DependencyScope.OPTIONAL: "optionnelle",
    }

    for item in result.dependencies:
        table.add_row(
            item.name,
            item.specifier or "—",
            scopes[item.scope],
            item.ecosystem,
            item.source,
        )
    return table


def _languages_table(result: CodeAudit) -> Table:
    """Répartition des langages telle qu'elle est sur disque."""
    table = Table(title="Langages", title_justify="left")
    table.add_column("Langage")
    table.add_column("Fichiers", justify="right")
    table.add_column("Code", justify="right")
    table.add_column("Commentaire", justify="right")

    for item in result.languages:
        table.add_row(
            item.language,
            str(item.files),
            str(item.lines.code),
            str(item.lines.comment),
        )
    return table


def _audit_table(results: Sequence[tuple[str, CodeAudit]]) -> Table:
    """Synthèse d'un audit portant sur plusieurs dépôts."""
    table = Table()
    table.add_column("Repository")
    table.add_column("Fichiers", justify="right")
    table.add_column("Code", justify="right")
    table.add_column("Langage")
    table.add_column("Fonctions", justify="right")
    table.add_column("Compl. moy.", justify="right")
    table.add_column("Tests", justify="right")
    table.add_column("Dépend.", justify="right")

    for full_name, result in results:
        average = result.average_complexity
        table.add_row(
            full_name,
            str(result.files_analysed),
            str(result.lines.code),
            result.languages[0].language if result.languages else "—",
            str(len(result.functions)),
            "—" if average is None else f"{average}",
            str(result.tests.files) if result.tests.exists else "[yellow]0[/yellow]",
            str(len(result.dependencies)),
        )
    return table


@app.command("advise")
def advise(
    repository: Annotated[
        str | None,
        typer.Argument(
            metavar="REPOSITORY",
            help="Dépôt à conseiller ; tous ceux de la base par défaut.",
        ),
    ] = None,
    save: Annotated[
        bool,
        typer.Option(
            "--save/--no-save",
            help="Enregistre les recommandations en base. Activé par défaut.",
        ),
    ] = True,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Modèle Ollama à interroger, au lieu de celui configuré."),
    ] = None,
) -> None:
    """Génère des recommandations prioritaires pour les dépôts, via Ollama.

    Githor priorise : l'ordre des recommandations vient des constats ouverts
    déjà enregistrés, triés par gravité, exactement comme dans un rapport.
    Ollama ne fait que rédiger un titre et une recommandation pour chacun,
    dans cet ordre — il ne choisit jamais quoi mettre en premier, ni ne
    commente rien qui ne soit pas déjà un constat connu.

    Comme le reste des commandes locales, elle relit la base et n'appelle
    jamais GitHub. Ollama tourne en local : aucune donnée du dépôt n'est
    envoyée à un service tiers.
    """
    config = current_config()
    rows = _stored_repositories(config, repository)
    model_name = model or config.ollama.model

    console.print("[bold]Githor — conseiller IA[/bold]\n")
    detailed = len(rows) == 1
    results: list[tuple[str, Advice]] = []
    skipped = 0
    failed = 0

    database = open_database(config)
    database.create_schema()

    with OllamaClient(config.ollama.host, timeout=config.ollama.timeout_seconds) as client:
        for row in rows:
            with database.session() as session:
                findings = latest_findings(session, row.id)
                score = compute_score(findings)
                audit_row = latest_audit(session, row.id)
                code = audit_metrics(session, audit_row) if audit_row is not None else None

            try:
                with stderr_console.status(f"Conseil IA pour {row.full_name}…"):
                    advice = generate_advice(
                        client,
                        repository=row,
                        findings=findings,
                        score=score,
                        code=code,
                        model=model_name,
                    )
            except OllamaError as exc:
                failed += 1
                stderr_console.print(f"[red]✗[/red] {row.full_name} : {exc}", highlight=False)
                continue

            if advice is None:
                skipped += 1
                console.print(
                    f"[dim]— {row.full_name} : rien à recommander (aucun constat ouvert).[/dim]",
                    highlight=False,
                )
                continue

            results.append((row.full_name, advice))

            stored = 0
            if save:
                with database.session() as session:
                    save_advice(session, row.id, advice)
                    stored = count_advice_runs(session, row.id)

            if detailed:
                _print_advice_detail(row.full_name, advice, runs=stored)
            else:
                console.print(
                    f"[green]✓[/green] {row.full_name} "
                    f"[dim]({len(advice.items)} recommandation(s))[/dim]",
                    highlight=False,
                )

    database.close()

    if not detailed and results:
        console.print()
        console.print(_advice_table(results))

    summary = f"\n[bold]{len(results)}[/bold] dépôt(s) conseillé(s)"
    if skipped:
        summary += f", {skipped} sans constat ouvert"
    console.print(summary + ".", highlight=False)
    if save and results:
        console.print(f"Base : {config.storage.database}", highlight=False)
    elif results:
        console.print("[dim]--no-save : rien n'a été écrit en base.[/dim]")
    if failed:
        console.print(f"[red]{failed} dépôt(s) en échec.[/red]", highlight=False)
        raise typer.Exit(code=1)


def _print_advice_detail(full_name: str, advice: Advice, *, runs: int = 0) -> None:
    """Détaille les recommandations d'un seul dépôt.

    Args:
        full_name: nom complet du dépôt.
        advice: recommandations à rendre.
        runs: nombre d'exécutions conservées, ``0`` si rien n'a été enregistré.
    """
    kept = f" · exécution {runs}" if runs else ""
    console.print(f"[bold]{full_name}[/bold] [dim]{kept}[/dim]\n", highlight=False)

    if advice.degraded:
        console.print(
            "[yellow]Réponse non structurée : recommandations conservées telles quelles, "
            "sans association aux constats.[/yellow]\n"
        )

    for item in advice.items:
        rule = f" [dim]({item.source_rule})[/dim]" if item.source_rule else ""
        console.print(f"[bold]{item.rank}. {item.title}[/bold]{rule}", highlight=False)
        console.print(item.recommendation, highlight=False)
        console.print()


def _advice_table(results: Sequence[tuple[str, Advice]]) -> Table:
    """Synthèse du conseil portant sur plusieurs dépôts."""
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("Repository")
    table.add_column("Recommandations", justify="right")
    table.add_column("Modèle")

    for full_name, advice in results:
        count = str(len(advice.items))
        table.add_row(
            full_name, f"[yellow]{count}[/yellow]" if advice.degraded else count, advice.model
        )
    return table


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


@app.command("report")
def report(
    repository: Annotated[
        str,
        typer.Argument(
            metavar="REPOSITORY",
            help="Dépôt dont on veut le rapport, par exemple Architecturor "
            "ou nouhailler/Architecturor.",
        ),
    ],
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            metavar="CHEMIN",
            help="Écrit le rapport dans ce fichier — ou, si le chemin est un "
            "répertoire existant, dans un fichier horodaté qu'il contient.",
        ),
    ] = None,
) -> None:
    """Produit le rapport Markdown d'un repository.

    Le rapport décrit le **dernier snapshot** enregistré : comme l'export, il
    relit la base et ne joint jamais GitHub. Sans ``--output``, il est écrit sur
    la sortie standard, telle quelle, afin de pouvoir être redirigé.
    """
    config = current_config()
    if not config.storage.database.exists():
        console.print(
            f"Aucune base à {config.storage.database} : lancez d'abord [bold]githor scan[/bold].",
            highlight=False,
        )
        raise typer.Exit(code=1)

    with open_database(config) as database:
        database.create_schema()
        with database.session() as session:
            row = find_repository_by_name(session, repository)
            if row is None:
                console.print(
                    f"[red]Repository inconnu de la base :[/red] {repository}", highlight=False
                )
                console.print("[dim]Voir githor findings sans argument.[/dim]")
                raise typer.Exit(code=1)
            document = build_report(session, row)

    if output is None:
        # Écriture directe : Rich habillerait et replierait le Markdown, ce qui
        # casserait les tableaux dès que le terminal est étroit.
        sys.stdout.write(render_report(document))
        return

    path = write_report(document, output)
    console.print(f"Rapport écrit dans : [bold]{path}[/bold]", highlight=False)


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


class CompareFormat(StrEnum):
    """Formats proposés par ``githor compare``."""

    TABLE = "table"
    JSON = "json"
    CSV = "csv"


@app.command("compare")
def compare(
    repository: Annotated[
        str | None,
        typer.Argument(
            metavar="[REPOSITORY]",
            help="Limite la comparaison à ce dépôt. Sans argument, tous les dépôts enregistrés.",
        ),
    ] = None,
    compare_format: Annotated[
        CompareFormat,
        typer.Option(
            "--format",
            "-f",
            case_sensitive=False,
            help="Format de sortie.",
        ),
    ] = CompareFormat.TABLE,
) -> None:
    """Compare les dépôts enregistrés sur un score dérivé de leurs constats (§34).

    Le score n'est stocké nulle part : il se recalcule à chaque appel depuis les
    findings du dernier snapshot de chaque dépôt — voir ``githor.scoring``. Deux
    appels successifs reflètent donc toujours l'état courant de la base.
    """
    config = current_config()
    rows = _stored_repositories(config, repository)

    with open_database(config) as database:
        database.create_schema()
        with database.session() as session:
            exports = [build_repository_export(session, row) for row in rows]

    if compare_format is CompareFormat.TABLE:
        console.print(_compare_table(exports))
        return

    dataset = Dataset(
        githor_version=__version__,
        generated_at=utc_now(),
        repository_count=len(exports),
        repositories=tuple(exports),
    )
    export_format = ExportFormat.JSON if compare_format is CompareFormat.JSON else ExportFormat.CSV
    sys.stdout.write(render(dataset, export_format))


def _compare_table(exports: Sequence[RepositoryExport]) -> Table:
    """Construit le tableau Project/Docs/Tests/CI/Security/Score, trié par score."""
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("Project")
    for label in ("Docs", "Tests", "CI", "Security", "Score"):
        table.add_column(label, justify="right")

    def rank(export: RepositoryExport) -> int:
        overall = export.score.overall if export.score else None
        return -1 if overall is None else overall

    for export in sorted(exports, key=rank, reverse=True):
        score = export.score
        table.add_row(
            export.full_name,
            _score_cell(score.docs if score else None),
            _score_cell(score.tests if score else None),
            _score_cell(score.ci if score else None),
            _score_cell(score.security if score else None),
            _score_cell(score.overall if score else None),
        )
    return table


def _score_cell(value: int | None) -> str:
    """Une case vide plutôt qu'un zéro trompeur, pour un groupe jamais évalué."""
    return "—" if value is None else f"{value} %"


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
