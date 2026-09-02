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
from rich.table import Table

from githor import __version__
from githor.collectors.repositories import RepositoryCollection, collect_repositories
from githor.config import Config, load_config
from githor.errors import GithorError
from githor.github.client import GitHubClient, RateLimit
from githor.github.token import find_token, require_token
from githor.logging import get_logger, setup_logging
from githor.models.repository import Repository
from githor.storage.database import Database

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
        user = client.get("/user")
        quota = client.get_rate_limit()

    login = user.get("login", "?") if isinstance(user, dict) else "?"

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
    scan = config.scan.model_copy(
        update={
            "include_forks": config.scan.include_forks or include_forks,
            "include_archived": config.scan.include_archived or include_archived,
        }
    )
    token = require_token(allow_gh_cli=config.github.use_gh_cli)

    with GitHubClient(token.value, api_url=config.github.api_url) as client:
        with stderr_console.status("Récupération des repositories…"):
            collection = collect_repositories(client, scan)
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
