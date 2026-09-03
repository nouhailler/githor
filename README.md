# Githor

Outil **local**, **CLI** et **lecture seule** permettant d'inventorier ses repositories
GitHub, d'en collecter les métadonnées, d'en suivre l'évolution dans le temps
(*snapshots*), d'en extraire des métriques et de détecter ce qui manque à chaque
projet (*findings*).

> **État : V0.1 en cours de développement — étapes 1 à 12 sur 13 terminées.**
> `githor scan` collecte métadonnées, langages, arborescence, activité,
> releases et issues, puis évalue les règles ; `githor findings` montre ce
> qui manque, `githor export` produit du JSON, du CSV et du Markdown, et
> `githor report` le rapport d'un dépôt. Reste la complétion de la suite
> de tests.

Pour aller plus loin : [CONTEXT.md](CONTEXT.md) explique les partis pris et
les invariants du projet, [CHANGELOG.md](CHANGELOG.md) retrace ce qui a été
livré étape par étape.

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
           ┌───────────┼───────────┐
           ▼           ▼           ▼
       Exporters    Reports    Future AI
      src/githor/  src/githor/
      exporters/     reports/
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
githor repos                      # liste les repositories accessibles
githor repos --include-forks      # y compris les forks
githor repos --include-archived   # y compris les dépôts archivés
githor scan                       # scanne tous les dépôts et enregistre un snapshot
githor scan Architecturor         # scanne un seul dépôt
githor findings                   # synthèse des constats de tous les dépôts
githor findings Architecturor     # détail d'un dépôt : ce qui est là, ce qui manque
githor export --format json       # export complet dans data/exports/
githor export -f csv              # une ligne par repository
githor export -f markdown         # inventaire lisible
githor report Architecturor       # rapport Markdown d'un dépôt, sur stdout
githor report NOM -o rapport.md   # le même rapport, écrit dans un fichier
githor db init                    # crée la base SQLite et son schéma
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

## Ce que le scan collecte

Six appels par dépôt, soit environ 470 requêtes pour 78 projets, sur un quota
horaire de 5 000.

| Collecte | Source | Stocké dans |
|---|---|---|
| Métadonnées | `/user/repos` | `repositories` + `repository_snapshots` |
| Langages | `/repos/…/languages` | `languages` (octets bruts **et** pourcentage) |
| Arborescence | `/repos/…/git/trees?recursive=1` | `repository_files` |
| Activité | `/repos/…/commits?since=…` | `commits` |
| Releases | `/repos/…/releases` | `releases` |
| Issues | `/repos/…/issues?state=all` | `issues` |
| Constats | aucune (règles locales) | `findings` |

Trois choix méritent d'être explicités :

- **l'arborescence est récupérée en un seul appel récursif**, et non répertoire
  par répertoire. Si GitHub la tronque, Githor l'annonce plutôt que de laisser
  croire à un décompte exact ;
- **seule la fenêtre configurée est téléchargée** (`commit_history_days`, 90 par
  défaut). Les décomptes sur 30 et 90 jours ne sont produits que si la fenêtre
  les couvre : une valeur absente vaut mieux qu'un chiffre faux ;
- **un commit est un fait daté** : il est ajouté s'il manque, jamais réécrit. Des
  scans qui se recouvrent ne créent donc aucun doublon. Releases et issues, elles,
  sont **mises à jour** : un brouillon finit par être publié, une issue par se
  fermer ;
- **les pull requests ne sont pas des issues**. GitHub les range dans la même
  collection ; Githor stocke les issues et se contente de compter les pull requests
  ouvertes, que le compteur `open_issues` de GitHub inclut à tort.

Un dépôt sans aucun commit — GitHub répond alors HTTP 409 — produit des collectes
vides, et non une erreur.

### Éléments détectés dans l'arborescence

Githor relève la présence de `README`, `LICENSE`, `CHANGELOG`, `CONTRIBUTING`,
`CODE_OF_CONDUCT`, `SECURITY`, `docs/`, `tests/`, `src/`, `.github/`,
`.github/workflows/`, `Dockerfile`, `compose.yaml`, `dependabot`, `package.json`,
les fichiers de verrouillage, `pyproject.toml`, `requirements.txt`,
`.editorconfig` et `.gitignore`.

La détection retient **le chemin qui a satisfait le marqueur**, pas un simple
booléen : un constat pourra ainsi toujours nommer le fichier sur lequel il se
fonde. La comparaison ignore la casse, mais pas l'orthographe — `Readme.MD` est
reconnu, `Licence` ne l'est pas.

## Périmètre du scan

Par défaut, les **forks** et les **dépôts archivés** sont exclus. Rien ne disparaît
en silence : le nombre d'exclusions est toujours affiché, avec le moyen de les
réintégrer.

```console
$ githor repos
Repository                Visibilité  Langage     ★  Issues  Dernier push
nouhailler/Architecturor  public      TypeScript  0       0  2026-08-24
nouhailler/Astror         public      JavaScript  0       0  2026-08-30
…

77 repository(s) sur 78 accessibles.
Exclus par le périmètre : 1 fork(s). Voir --include-forks / --include-archived.
```

Le périmètre se règle durablement dans la configuration, ou ponctuellement par
options :

```toml
[scan]
include_forks = false
include_archived = false
```

La progression et les journaux partent sur `stderr` : `githor repos > liste.txt`
produit un fichier exploitable.

## Structure du projet

```text
├── pyproject.toml
├── README.md        # ce que fait Githor, et comment s'en servir
├── CONTEXT.md       # pourquoi il est construit ainsi : partis pris, invariants
├── CHANGELOG.md     # ce qui a été livré, étape par étape
├── CAHIER-DES-CHARGES.md  # la spécification d'origine de la V0.1
├── LICENSE
├── .gitignore
│
├── src/githor/
│   ├── cli.py        # options globales, logging, traduction des erreurs
│   ├── config.py     # lecture et validation du TOML, résolution des chemins
│   ├── errors.py     # exceptions applicatives (messages destinés à l'utilisateur)
│   ├── logging.py    # configuration du logging (Rich, stderr)
│   ├── github/       # client HTTP, résolution du jeton, erreurs
│   ├── models/       # repository, snapshot, activity, release, issue, finding
│   ├── collectors/   # repositories, langages, structure, activité, releases, issues
│   ├── rules/        # catalogue de règles et moteur d'évaluation
│   ├── storage/      # SQLAlchemy : schéma (tables.py) et session (database.py)
│   ├── exporters/    # jeu de données, métriques dérivées, JSON, CSV, Markdown
│   ├── reports/      # rapport Markdown d'un seul dépôt
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

## Base de données

La base vit dans `data/githor.db` (chemin configurable) et n'est jamais versionnée.

```bash
githor db init     # idempotent : crée ce qui manque, ne détruit rien
```

Huit tables :

| Table | Rattachée à | Contenu |
|---|---|---|
| `repositories` | — | le projet : identité, adresses, dates, statut |
| `repository_snapshots` | repository | son état mesuré à une date donnée |
| `languages` | snapshot | octets et pourcentage par langage |
| `repository_files` | snapshot | arborescence relevée |
| `commits` | repository | commits de la fenêtre d'historique |
| `releases` | repository | tag, nom, date, brouillon, préversion |
| `issues` | repository | numéro, titre, état, dates |
| `findings` | repository + snapshot | constats produits par les règles |

Deux choix structurent ce schéma :

- **langages et fichiers pendent du snapshot**, pas du repository : leur évolution
  reste ainsi lisible dans le temps ;
- **commits, releases et issues pendent du repository** : ce sont des faits datés,
  qui ne se réécrivent pas d'un scan à l'autre.

Les clés étrangères sont réellement appliquées — SQLite les ignore par défaut, et
Githor émet `PRAGMA foreign_keys = ON` sur chaque connexion. Supprimer un
repository supprime donc tout son historique. Les dates sont stockées en UTC et
relues en UTC, quel que soit le fuseau de la machine.

## Snapshots

Un `Repository` représente le projet ; un `RepositorySnapshot` représente son état
à une date donnée. Un nouveau scan **ajoute** un snapshot, il ne remplace pas les
précédents — c'est ce qui permettra d'étudier l'évolution des projets et de les
comparer entre eux.

```console
$ githor scan
Githor — scan

Repositories à scanner : 77

+ nouhailler/Architecturor (snapshot 1 · 76 fichiers · 3 langages · 190 commits/90j · 5 constats)
+ nouhailler/Astror (snapshot 1 · 164 fichiers · 4 langages · 94 commits/90j · 3 constats)
…

77 repository(s) scanné(s) : 77 nouveau(x), 0 mis à jour.
77 snapshot(s), 286 langage(s), 9827 entrée(s) d'arborescence, 1242 commit(s) ajouté(s).
770 constat(s) évalué(s), dont 502 ouvert(s).
```

`+` signale un dépôt découvert, `✓` un dépôt déjà connu ; le compteur entre
parenthèses indique combien de mesures Githor conserve pour ce projet.

Le dépôt est identifié par son **identifiant GitHub**, jamais par son nom : un
projet renommé reste le même projet, et son historique le suit.

```bash
githor scan                  # tous les dépôts du périmètre
githor scan Architecturor    # un seul, nom court rattaché à votre compte
githor scan autrui/projet    # un seul, nom complet
```

## Findings

Un *finding* est un constat **déterministe** : une règle, un fait, un chemin. Aucune
IA, aucune heuristique floue — `documentation.changelog` signifie exactement
« aucun fichier CHANGELOG n'a été trouvé dans l'arborescence relevée ».

```console
$ githor findings
Repository                    Snapshot    ✓  ✗  Élevée  Moyenne  Faible
nouhailler/Architecturor      2026-09-03  5  5       1        1       3
nouhailler/Astror             2026-09-03  7  3       0        1       2
…

502 constat(s) ouvert(s) sur 77 repository(s).
```

```console
$ githor findings Architecturor
nouhailler/Architecturor
Snapshot du 2026-09-03

Documentation
────────────────────────────────
README              ✓
LICENSE             ✗
CHANGELOG           ✓
CONTRIBUTING        ✗
docs/               ✓

Development
────────────────────────────────
tests/              ✗
GitHub Actions      ✓

Constats ouverts (5)

Élevée
  • tests/ absent. (development.tests)
    → Ajouter un répertoire de tests : sans tests, aucune évolution n'est vérifiable.
```

`githor findings` ne joint pas GitHub : il relit la base produite par le scan.

### Règles de la V0.1

| Règle | Gravité si absent | Fondement |
|---|---|---|
| `documentation.readme` | élevée | fichier `README*` |
| `documentation.license` | moyenne | fichier `LICENSE` / `COPYING` |
| `documentation.changelog` | moyenne | fichier `CHANGELOG*` |
| `documentation.contributing` | faible | fichier `CONTRIBUTING*` |
| `documentation.docs` | faible | répertoire `docs/` ou `doc/` |
| `development.tests` | élevée | répertoire `tests/`, `test/` ou `spec/` |
| `development.github_actions` | moyenne | répertoire `.github/workflows/` |
| `infrastructure.docker` | faible | `Dockerfile` ou `Containerfile` |
| `infrastructure.dependabot` | faible | `.github/dependabot.yml` |
| `maintenance.activity` | moyenne | aucun push depuis 180 jours |

Un dépôt **archivé** ne se voit pas reprocher son inactivité : son immobilité est
voulue.

### Deux partis pris

- **les règles satisfaites sont enregistrées elles aussi**, avec le statut `ok` et la
  gravité `info`. La table `findings` contient donc, pour chaque snapshot, l'état
  complet de ce qui a été vérifié : la synthèse ✓/✗ se reconstitue à partir de la
  seule base, et l'on saura plus tard **à quelle date** un projet a gagné son
  CHANGELOG ;
- **un constat cite ce sur quoi il se fonde**. `README présent : README.md` nomme le
  fichier trouvé, jamais un simple booléen.

### Ajouter une règle

Rien n'est codé en dur dans la CLI : une règle est une entrée du catalogue
(`src/githor/rules/catalog.py`). Pour la majorité des cas — « le fichier attendu
est-il là ? » — il suffit d'une déclaration :

```python
MarkerRule(
    id="documentation.security",
    category="documentation",
    label="SECURITY",
    severity=Severity.LOW,
    marker="security",  # marqueur défini dans collectors/structure.py
    recommendation="Ajouter un SECURITY.md décrivant le signalement des failles.",
)
```

Une règle qui demande une logique propre dérive de `Rule` et implémente `check()`,
comme `InactivityRule`. Elle ne connaît ni GitHub ni SQLite : elle lit un
`RuleContext` déjà collecté et rend un `Verdict`.

## Exports

```bash
githor export --format json       # tout, sans perte
githor export --format csv        # une ligne par repository, pour un tableur
githor export --format markdown   # un inventaire qui se lit
githor export -f json -o /tmp     # ailleurs que dans data/exports/
```

Un export décrit le **dernier snapshot** de chaque dépôt : il ne joint pas GitHub,
et ne dépend donc ni du réseau ni du quota. Le fichier produit est **horodaté**
(`githor-20260903-083426.json`) : un export n'écrase jamais le précédent, au même
titre qu'un snapshot n'écrase pas la mesure d'avant.

| Format | Contient | Sert à |
|---|---|---|
| JSON | tout : métadonnées, snapshot, métriques, langages, constats, releases | rejouer, comparer, alimenter un autre outil |
| CSV | une ligne par dépôt, valeurs aplaties en décomptes | trier, filtrer, ouvrir dans un tableur |
| Markdown | vue d'ensemble, constats les plus fréquents, détail par dépôt | se lire |

Le Markdown ouvre sur ce qui est le plus utile quand on a 78 projets — ce qui
manque le plus souvent, donc ce qu'on gagnerait à corriger une fois pour toutes :

```markdown
| Règle | Gravité | Dépôts concernés |
|---|---|---:|
| `infrastructure.dependabot` | Faible | 78 |
| `development.tests` | Élevée | 72 |
| `documentation.license` | Moyenne | 63 |
```

### Métriques

Les métriques ne sont pas stockées : elles sont **dérivées** des tables au moment
de l'export, ce qui évite qu'un chiffre et sa source divergent. Fichiers,
répertoires et langages viennent du dernier snapshot ; les fenêtres de commits
sont comptées depuis la **date du snapshot**, et non depuis l'instant de
l'export, pour qu'un même snapshot produise toujours le même chiffre.

## Rapports

```bash
githor report Architecturor              # sur la sortie standard
githor report Architecturor > rap.md     # redirigé
githor report Architecturor -o rap.md    # dans un fichier nommé
githor report Architecturor -o data/exports   # fichier horodaté dans un répertoire
```

Là où l'export décrit le parc entier, un rapport répond à une seule question :
*où en est ce projet-là ?* Il se construit depuis la **même base** et le **même
jeu de données** que l'export — il ne joint donc jamais GitHub — et décrit le
dernier snapshot enregistré, en le datant.

Le document enchaîne ce que l'on demande à un projet qu'on redécouvre : ses
mesures, ses langages, ce qui a été vérifié, ce qui manque, et depuis quand il
est suivi.

```markdown
# nouhailler/Architecturor

*Rapport Githor 0.1.0 — généré le 2026-09-03 19:59 UTC, d'après le snapshot du 2026-09-03 08:31 UTC.*

<https://github.com/nouhailler/Architecturor> — public · branche `main`

## Vue d'ensemble

| Metric | Value |
|---|---:|
| Files | 76 |
| Languages | 3 |
| Commits (30 d) | 153 |

## Documentation

| Item | Status |
|---|---|
| README | ✓ |
| LICENSE | ✗ |
| CHANGELOG | ✓ |

## Constats

### Élevée

- tests/ absent. (`development.tests`) — *Ajouter un répertoire de tests…*
```

Les vérifications sont rendues depuis les **constats enregistrés**, jamais depuis
le catalogue courant : un rapport montre ce qui avait été vérifié à la date du
snapshot, et non ce que Githor saurait vérifier aujourd'hui.

Sans `--output`, le Markdown part **tel quel** sur `stdout` : il n'est ni habillé
ni replié, afin qu'un tableau reste intact dans un terminal étroit comme dans un
tube. Avec un répertoire existant en `--output`, le fichier produit est horodaté
(`githor-report-nouhailler-Architecturor-20260903-195935.md`) et n'écrase jamais
le précédent.

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
