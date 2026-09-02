# Githor

Outil **local**, **CLI** et **lecture seule** permettant d'inventorier ses repositories
GitHub, d'en collecter les métadonnées, d'en suivre l'évolution dans le temps
(*snapshots*), d'en extraire des métriques et de détecter ce qui manque à chaque
projet (*findings*).

> **État : V0.1 en cours de développement — étapes 1 à 5 sur 13 terminées.**
> `githor auth check` fonctionne. Les commandes d'inventaire et de scan
> décrites dans « Utilisation » arrivent aux étapes suivantes.

---

## Principes

- **Local** — aucune donnée n'est envoyée ailleurs que vers l'API GitHub.
- **Lecture seule** — la V0.1 ne modifie aucun repository, issue, branche ou fichier.
- **Déterministe** — aucune IA dans la V0.1 ; un *finding* s'explique toujours par un fait vérifiable.
- **Sans secret stocké** — le token vit uniquement dans `GITHUB_TOKEN`, jamais dans le dépôt,
  jamais dans SQLite, jamais dans les exports.
- **Historique préservé** — chaque scan crée un snapshot, il n'écrase pas le précédent.

## Architecture

```text
                    GitHub
                       │
                       ▼
                GitHub Adapter        src/githor/github/
                       │
                       ▼
              Normalized Models       src/githor/models/
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
       Metrics      Findings      Snapshot
          │            │            │
          └────────────┼────────────┘
                       ▼
                     SQLite          src/githor/storage/
                       │
              ┌────────┴────────┐
              ▼                 ▼
          Exporters          Future AI
      src/githor/exporters/
```

La couche `github/` ne connaît ni la base de données ni la CLI. Les `collectors/`
font le pont entre l'API et les modèles normalisés.

## Prérequis

- Debian 13 (ou équivalent)
- Python **3.13+**
- Un *personal access token* GitHub en lecture seule (aucun scope d'écriture n'est
  nécessaire ; `repo` en lecture suffit pour atteindre les dépôts privés)

## Installation (Debian)

Debian applique la [PEP 668](https://peps.python.org/pep-0668/) : l'installation doit
se faire dans un environnement virtuel.

```bash
git clone https://github.com/nouhailler/Githor.git
cd Githor

python3 -m venv .venv
source .venv/bin/activate

pip install -e .
```

Pour développer et lancer les tests :

```bash
pip install -e ".[dev]"
```

Vérification immédiate :

```bash
githor --version
githor --help
```

## Configuration

### Jeton GitHub

Githor cherche un jeton dans cet ordre :

1. la variable d'environnement **`GITHUB_TOKEN`** ;
2. la **CLI `gh`**, via `gh auth token`, si elle est installée et authentifiée.

Si vous utilisez déjà `gh` pour vos `git push`, **vous n'avez rien à faire** :

```bash
gh auth login     # une seule fois, si ce n'est pas déjà fait
githor auth check
```

> ⚠️ **N'exportez pas `GITHUB_TOKEN` en permanence dans `~/.bashrc`.** `gh` lit
> lui-même cette variable et la préfère à son trousseau : un jeton restreint
> exporté là ferait échouer vos `git push`, ainsi que tout autre outil s'appuyant
> sur `gh`. Le repli automatique existe précisément pour éviter cet export.

Pour n'utiliser qu'un jeton explicite — par exemple un *fine-grained PAT* en
lecture seule — fournissez-le le temps d'une commande, et désactivez le repli :

```bash
GITHUB_TOKEN="github_pat_..." githor auth check     # ponctuel, non exporté
```

```toml
[github]
use_gh_cli = false
```

Le jeton n'est jamais affiché, journalisé, ni persisté : seule sa **provenance**
apparaît (`configuré (gh CLI)`, `configuré (GITHUB_TOKEN)`, `absent`).

### Fichier de configuration

Toutes les clés sont facultatives : sans fichier, Githor applique ses valeurs par
défaut. Pour personnaliser :

```bash
cp config/config.toml.example config/config.toml
```

```toml
[github]
api_url = "https://api.github.com"   # racine de l'API (GitHub Enterprise possible)
use_gh_cli = true                    # repli sur « gh auth token » si GITHUB_TOKEN est absent

[scan]
include_forks = false
include_archived = false
commit_history_days = 90             # profondeur d'historique, 1 à 3650 jours

[storage]
database = "data/githor.db"

[export]
directory = "data/exports"
```

`config/config.toml` est ignoré par git : il vous appartient.

**Ordre de recherche**, du plus prioritaire au moins prioritaire :

1. `githor --config <fichier>` ;
2. la variable d'environnement `GITHOR_CONFIG` ;
3. `config/config.toml` sous le répertoire de travail ;
4. `~/.config/githor/config.toml`.

Les **chemins relatifs** sont résolus depuis le répertoire de travail courant —
une seule règle, sans cas particulier. Une **clé inconnue** (faute de frappe,
section obsolète) provoque une erreur explicite plutôt qu'un silence :

```console
$ githor --config perso.toml config show
Erreur : Configuration invalide dans perso.toml :
  - scan.include_fork : Extra inputs are not permitted
```

Pour vérifier la configuration réellement appliquée :

```bash
githor config show
```

## Utilisation

### Disponible aujourd'hui

```bash
githor --help                     # aide générale
githor --version                  # version de Githor
githor auth check                 # vérifie le jeton, l'API et le quota
githor config show                # configuration effective et provenance du jeton
githor --config f.toml <cmd>      # utilise un fichier de configuration précis
githor --debug <commande>         # logs détaillés et traceback complète en cas d'erreur
```

```console
$ githor auth check
Authentification GitHub

Jeton        configuré (gh CLI)
Utilisateur  nouhailler
API          joignable (https://api.github.com)
Quota        5000 / 5000
Statut       OK
```

### Cible de la V0.1

```bash
githor repos                      # liste les repositories accessibles
githor scan                       # scanne tout et alimente SQLite
githor scan Architecturor         # scanne un seul repository
githor report Architecturor       # rapport Markdown d'un repository
githor export --format json       # exports dans data/exports/
githor export --format csv
githor export --format markdown
```

## Structure du projet

```text
├── pyproject.toml
├── README.md
├── LICENSE
├── .gitignore
│
├── src/githor/
│   ├── cli.py        # options globales, logging, traduction des erreurs
│   ├── config.py     # lecture et validation du TOML, résolution des chemins
│   ├── errors.py     # exceptions applicatives (messages destinés à l'utilisateur)
│   ├── logging.py    # configuration du logging (Rich, stderr)
│   ├── github/       # client HTTP, résolution du jeton, erreurs
│   ├── models/       # modèles normalisés (repository, snapshot, activity)
│   ├── collectors/   # repositories, languages, structure, activity
│   ├── storage/      # SQLAlchemy : schéma et accès SQLite
│   ├── exporters/    # JSON, CSV, Markdown
│   └── utils/        # dates et helpers
│
├── tests/            # suite pytest — les appels GitHub sont toujours mockés
├── config/           # config.toml.example
└── data/             # base SQLite, exports, cache (non versionnés)
```

## Accès à l'API GitHub

La couche `github/` isole tout ce qui touche au réseau :

- **authentification** par en-tête `Authorization: Bearer`, version d'API épinglée
  (`X-GitHub-Api-Version`), `User-Agent` identifiant Githor ;
- **pagination** guidée par l'en-tête `Link` renvoyé par GitHub, jamais par un compteur
  calculé localement, avec une borne de sécurité qui avertit plutôt que de boucler ;
- **tentatives bornées** : délais, coupures réseau et erreurs 5xx sont réessayés avec une
  attente exponentielle, `Retry-After` étant respecté quand GitHub l'impose ;
- **quota** relevé à chaque réponse. Un quota bas déclenche un avertissement ; un quota
  **épuisé arrête immédiatement** le programme en indiquant l'heure de réinitialisation —
  Githor n'attend jamais une heure en silence ;
- **erreurs nommées** : `AuthenticationError`, `PermissionError`, `NotFoundError`,
  `RateLimitError`, `APIUnavailableError`, `TimeoutError`, `InvalidResponseError`,
  `UnexpectedStatusError`, toutes dérivées de `GithorError`.

## Snapshots

Un `Repository` représente le projet ; un `RepositorySnapshot` représente son état
à une date donnée. Un nouveau scan **ajoute** un snapshot, il ne remplace pas les
précédents — c'est ce qui permettra plus tard d'étudier l'évolution des projets et
de comparer les repositories entre eux.

## Logs et diagnostic

Les logs partent sur `stderr`, afin que `stdout` reste réservé aux données produites
(exports, rapports). Par défaut, seuls les avertissements et les erreurs s'affichent ;
`--debug` abaisse le niveau à `DEBUG` et rétablit la traceback complète.

Les erreurs attendues (token manquant, configuration invalide, dépôt inaccessible)
dérivent toutes de `GithorError` et s'affichent comme un message, sans pile d'appels ;
le code de sortie est `1`, ou `130` après un Ctrl-C.

## Tests

```bash
source .venv/bin/activate
pytest          # suite de tests
ruff check .    # lint
mypy            # typage statique
```

Les tests n'utilisent **jamais** de token GitHub réel : le transport HTTP est mocké.

## Roadmap

| Version | Contenu |
|---|---|
| **V0.1** | Inventaire, métadonnées, langages, structure, activité, issues, releases, métriques, findings, snapshots, exports, rapports |
| V0.2 | Code Auditor : clone local, AST, LOC, complexité, imports, dépendances |
| V0.3 | Project Intelligence : comparaison inter-projets, scores, historique |
| V0.4 | AI Advisor : analyse via Ollama, recommandations priorisées |

## Licence

MIT — voir [LICENSE](LICENSE).
