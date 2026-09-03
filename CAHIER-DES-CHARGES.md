# Projet : Githor — V0.1

## 1. Objectif

Je souhaite développer sous Debian un outil Python personnel appelé **Githor**.

L'objectif à terme est de pouvoir analyser l'ensemble de mes projets GitHub afin de :

- inventorier tous mes repositories ;
- récupérer leurs métadonnées ;
- suivre leur évolution dans le temps ;
- exporter des métriques ;
- analyser leur structure ;
- détecter ce qui manque dans chaque projet ;
- comparer plusieurs projets entre eux ;
- identifier des bonnes pratiques présentes dans certains projets mais absentes dans d'autres ;
- à terme, utiliser un LLM local via Ollama pour analyser les projets et proposer des améliorations.

**Important : nous développons uniquement le V0.1 pour le moment.**

Le V0.1 doit constituer un socle propre, modulaire et extensible pour les futures versions.

---

# 2. Philosophie du projet

L'application doit être :

- locale ;
- open source ;
- orientée CLI ;
- simple à installer sous Debian ;
- robuste ;
- modulaire ;
- testable ;
- documentée ;
- conçue pour évoluer ;
- sans dépendance inutile à des services cloud supplémentaires.

GitHub est utilisé comme source de données.

Le V0.1 est **lecture seule** : aucune modification de repository, issue, branche, fichier ou configuration GitHub ne doit être effectuée.

---

# 3. Architecture générale

L'architecture doit suivre ce principe :

```text
                    GitHub
                       │
                       ▼
                GitHub Adapter
                       │
                       ▼
              Normalized Models
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
       Metrics      Findings      Snapshot
          │            │            │
          └────────────┼────────────┘
                       ▼
                     SQLite
                       │
              ┌────────┴────────┐
              ▼                 ▼
          Exporters          Future AI
```

Le code doit être organisé afin que la couche GitHub soit indépendante du reste de l'application.

---

# 4. Technologies imposées

Utiliser :

- Python 3.13+
- `httpx` pour l'accès HTTP à l'API GitHub
- `Typer` pour la CLI
- `Rich` pour l'affichage terminal
- `SQLAlchemy` pour SQLite
- `Pydantic` pour les modèles/validation lorsque pertinent
- `pytest` pour les tests
- TOML pour la configuration
- `logging` pour les logs
- `subprocess` pour les éventuelles opérations Git futures

Utiliser un `pyproject.toml` moderne.

Utiliser un environnement virtuel Python classique (`venv`).

Ne pas utiliser PyGithub pour le moment : je veux une couche API GitHub explicite et contrôlée avec `httpx`.

---

# 5. Arborescence souhaitée

Créer une structure proche de :

```text
githor/
│
├── pyproject.toml
├── README.md
├── LICENSE
├── .gitignore
│
├── src/
│   └── github_auditor/
│       │
│       ├── __init__.py
│       ├── cli.py
│       ├── config.py
│       ├── logging.py
│       │
│       ├── github/
│       │   ├── __init__.py
│       │   ├── client.py
│       │   ├── repositories.py
│       │   └── errors.py
│       │
│       ├── models/
│       │   ├── __init__.py
│       │   ├── repository.py
│       │   ├── snapshot.py
│       │   └── activity.py
│       │
│       ├── collectors/
│       │   ├── __init__.py
│       │   ├── repositories.py
│       │   ├── languages.py
│       │   ├── structure.py
│       │   └── activity.py
│       │
│       ├── storage/
│       │   ├── __init__.py
│       │   ├── database.py
│       │   └── repositories.py
│       │
│       ├── exporters/
│       │   ├── __init__.py
│       │   ├── json.py
│       │   ├── csv.py
│       │   └── markdown.py
│       │
│       └── utils/
│           ├── __init__.py
│           └── dates.py
│
├── tests/
│   ├── test_github_client.py
│   ├── test_collectors.py
│   ├── test_storage.py
│   └── test_exporters.py
│
├── data/
│   ├── githor.db
│   ├── exports/
│   └── cache/
│
└── config/
    └── config.toml.example
```

Tu peux adapter légèrement cette structure si une meilleure organisation est techniquement justifiée, mais conserve impérativement la séparation :

```text
github
collectors
models
storage
exporters
```

---

# 6. Authentification GitHub

Le token GitHub ne doit jamais être stocké dans le repository.

Utiliser une variable d'environnement :

```bash
GITHUB_TOKEN
```

Prévoir une commande :

```bash
githor auth check
```

Cette commande doit :

1. vérifier que `GITHUB_TOKEN` existe ;
2. contacter GitHub ;
3. vérifier que l'authentification fonctionne ;
4. afficher l'utilisateur GitHub authentifié ;
5. afficher les informations utiles sur le rate limit.

Exemple :

```text
GitHub authentication

Token: configured
User: myusername
API: reachable
Rate limit: 4980 / 5000
Status: OK
```

Ne jamais afficher le token.

---

# 7. Client GitHub

Créer :

```text
src/github_auditor/github/client.py
```

Créer une classe :

```python
GitHubClient
```

Responsabilités :

- authentification ;
- headers HTTP ;
- timeout ;
- gestion des erreurs ;
- pagination ;
- rate limiting ;
- retries raisonnables ;
- logging.

Prévoir notamment :

```python
get()
get_paginated()
```

Le client doit pouvoir exploiter l'API REST GitHub directement.

Utiliser une URL de base configurable :

```text
https://api.github.com
```

---

# 8. API GitHub à utiliser dans le V0.1

Le V0.1 doit au minimum exploiter les informations suivantes.

## Liste des repositories

Endpoint logique :

```text
GET /user/repos
```

Récupérer tous les repositories accessibles à l'utilisateur.

Prévoir la pagination.

Permettre de configurer :

```toml
[scan]
include_forks = false
include_archived = false
```

Par défaut :

- exclure les forks ;
- exclure les repositories archivés.

---

# 9. Métadonnées Repository

Créer un modèle `Repository`.

Stocker au minimum :

```text
id
github_id
name
full_name
owner
description

html_url
clone_url
ssh_url

visibility
default_branch

created_at
updated_at
pushed_at

size
language

fork
archived
disabled

has_issues
has_projects
has_wiki
has_pages
has_discussions

open_issues_count

stars
forks
watchers

license
topics
```

Conserver les dates correctement sous forme de datetime.

Ne pas perdre les identifiants GitHub.

---

# 10. Concept de Snapshot

Il s'agit d'un élément architectural important.

Ne pas simplement écraser les informations existantes à chaque scan.

Créer un concept de :

```text
Repository
RepositorySnapshot
```

Un repository représente le projet.

Un snapshot représente son état à une date donnée.

Exemple :

```text
Architecturor

Snapshot
2026-08-01

Snapshot
2026-08-15

Snapshot
2026-09-02
```

Cela permettra plus tard d'étudier l'évolution des projets.

---

# 11. Base SQLite

Utiliser SQLAlchemy.

Créer au minimum les tables suivantes :

## repositories

```text
id
github_id
full_name
name
owner
description
url
default_branch
visibility
created_at
updated_at
pushed_at
archived
fork
```

## repository_snapshots

```text
id
repository_id
collected_at

stars
forks
watchers

open_issues
open_prs

size_kb

primary_language
default_branch
```

## languages

```text
id
snapshot_id
language
bytes
percentage
```

## repository_files

```text
id
snapshot_id
path
type
size
```

## commits

```text
id
repository_id
sha
author
message
committed_at
```

## releases

```text
id
repository_id
tag
name
published_at
draft
prerelease
```

## issues

```text
id
repository_id
github_id
number
title
state
created_at
updated_at
closed_at
```

Ajouter les clés étrangères et index pertinents.

Éviter les duplications inutiles.

---

# 12. Collector repositories

Créer :

```text
collectors/repositories.py
```

Responsabilité :

```text
GitHub API
   ↓
repositories
   ↓
normalisation
   ↓
models
   ↓
storage
```

La commande :

```bash
githor repos
```

doit afficher les repositories disponibles.

Exemple :

```text
Repositories

Astror
Architecturor
Sociologor
Psychologor
...
```

---

# 13. Collector languages

Créer :

```text
collectors/languages.py
```

Utiliser l'API GitHub permettant de récupérer les langages du repository.

Exemple de résultat :

```text
Architecturor

TypeScript    72.4%
CSS           17.8%
HTML           7.1%
JSON           2.7%
```

Stocker les valeurs brutes en bytes ainsi que le pourcentage calculé.

---

# 14. Collector structure

Créer :

```text
collectors/structure.py
```

Le but est de récupérer l'arborescence du repository.

Détecter notamment :

```text
README.md
README
LICENSE
LICENSE.md

CHANGELOG.md
CHANGELOG

CONTRIBUTING.md
CODE_OF_CONDUCT.md
SECURITY.md

docs/
tests/
test/
src/

.github/
.github/workflows/

Dockerfile
compose.yaml
docker-compose.yml

package.json
package-lock.json
pnpm-lock.yaml

pyproject.toml
requirements.txt
```

Ne pas encore analyser le contenu des fichiers.

Le V0.1 s'intéresse principalement à leur présence et leur structure.

---

# 15. Collector activity

Créer :

```text
collectors/activity.py
```

Récupérer au minimum :

- date du dernier commit ;
- commits récents ;
- activité sur les 30 derniers jours ;
- activité sur les 90 derniers jours.

La période doit être configurable.

Par défaut :

```toml
[scan]
commit_history_days = 90
```

Ne pas télécharger inutilement toute l'histoire des repositories.

---

# 16. Releases

Récupérer les releases GitHub.

Stocker :

- tag ;
- nom ;
- date ;
- draft ;
- prerelease.

Cela permettra plus tard d'analyser la maturité des releases.

---

# 17. Issues

Récupérer les issues ouvertes/fermées nécessaires au V0.1.

Stocker :

- identifiant GitHub ;
- numéro ;
- titre ;
- état ;
- dates.

Les pull requests peuvent être comptées dans les métriques, mais une analyse détaillée des PR n'est pas obligatoire dans le V0.1.

---

# 18. Metrics

Créer une première couche de métriques.

Métriques minimales :

## Repository

```text
stars
forks
issues
```

## Code

```text
repository size
file count
directory count
languages
```

## Activity

```text
last commit
commits / 30 days
commits / 90 days
```

## Documentation

Détecter :

```text
README
LICENSE
CHANGELOG
CONTRIBUTING
docs
```

## Infrastructure

Détecter la présence de :

```text
.github/workflows/
Dockerfile
compose.yaml
Dependabot configuration
```

---

# 19. Findings

Créer dès le V0.1 un mécanisme générique de `Finding`.

C'est essentiel pour les futures versions.

Un Finding doit pouvoir contenir :

```text
repository
snapshot
category
rule
severity
status
message
recommendation
```

Exemple :

```text
Repository: Architecturor
Category: documentation
Rule: missing_changelog
Severity: medium
Status: open

Message:
Le projet ne possède pas de CHANGELOG.md.

Recommendation:
Ajouter un changelog.
```

---

# 20. Rules

Créer une architecture permettant d'ajouter des règles facilement.

Exemples de règles V0.1 :

```text
documentation.readme
documentation.license
documentation.changelog
documentation.contributing
documentation.docs

development.tests
development.github_actions

infrastructure.docker

maintenance.activity
```

Une règle doit produire un Finding.

Exemple :

```text
README présent → OK
README absent → WARNING
```

Ne pas coder toutes les règles en dur dans la CLI.

---

# 21. Première logique "ce qui manque"

Pour chaque repository, produire une synthèse :

```text
Documentation
────────────────────────────
README             ✓
LICENSE            ✓
CHANGELOG          ✗
CONTRIBUTING       ✗
docs/              ✗

Development
────────────────────────────
tests/             ✓
GitHub Actions     ✓

Infrastructure
────────────────────────────
Docker             ✗
Dependabot         ✗
```

Les règles doivent être explicites et déterministes.

Pas d'IA dans cette partie.

---

# 22. Commande principale

Créer :

```bash
githor scan
```

Elle doit :

1. vérifier la configuration ;
2. vérifier GitHub ;
3. récupérer les repositories ;
4. filtrer selon la configuration ;
5. récupérer les informations nécessaires ;
6. créer ou mettre à jour le repository ;
7. créer un snapshot ;
8. récupérer les langages ;
9. récupérer la structure ;
10. récupérer l'activité ;
11. récupérer releases/issues ;
12. calculer les Findings ;
13. sauvegarder dans SQLite ;
14. afficher un résumé.

Afficher une progression avec Rich.

Exemple :

```text
Githor

Connecting to GitHub... ✓

Repositories found: 18

Scanning:

✓ Astror
✓ Architecturor
✓ Sociologor
✓ Psychologor
...

18 repositories scanned.

Database updated.
```

---

# 23. Scan individuel

Prévoir :

```bash
githor scan Architecturor
```

Cette commande doit scanner uniquement le repository demandé.

Si le repository n'existe pas :

```text
Repository not found: Architecturor
```

avec un code de sortie non nul.

---

# 24. Reporting

Créer :

```text
exporters/
```

Prévoir trois formats.

## JSON

```bash
githor export --format json
```

## CSV

```bash
githor export --format csv
```

## Markdown

```bash
githor export --format markdown
```

Les exports doivent être écrits dans :

```text
data/exports/
```

---

# 25. Rapport Markdown individuel

Pour :

```bash
githor report Architecturor
```

produire un rapport similaire à :

```markdown
# Architecturor

## Overview

| Metric | Value |
|---|---:|
| Files | 428 |
| Languages | 3 |
| Stars | 0 |
| Forks | 0 |
| Open issues | 4 |

## Languages

- TypeScript — 72.4%
- CSS — 17.8%
- HTML — 7.1%

## Documentation

| Item | Status |
|---|---|
| README | ✓ |
| LICENSE | ✓ |
| CHANGELOG | ✗ |
| CONTRIBUTING | ✗ |
| docs/ | ✗ |

## Findings

### Medium

- CHANGELOG missing

### Low

- CONTRIBUTING.md missing
```

Le rapport doit être généré à partir des données SQLite.

---

# 26. Configuration

Créer :

```text
config/config.toml.example
```

avec :

```toml
[github]
api_url = "https://api.github.com"

[scan]
include_forks = false
include_archived = false
commit_history_days = 90

[storage]
database = "data/githor.db"

[export]
directory = "data/exports"
```

Prévoir une configuration utilisateur locale sans secret.

---

# 27. Cache et rate limit

Le système doit éviter autant que possible les requêtes inutiles.

SQLite peut servir de cache logique via les snapshots.

Avant de rescanner une donnée, prévoir une stratégie permettant de savoir si un snapshot récent existe.

Le client GitHub doit surveiller le rate limit.

Afficher une information utile si le quota devient faible.

Ne jamais boucler indéfiniment en cas de rate limit.

---

# 28. Gestion des erreurs

Le programme doit gérer proprement :

- token absent ;
- token invalide ;
- repository inaccessible ;
- repository supprimé ;
- API indisponible ;
- timeout ;
- réponse HTTP inattendue ;
- rate limit ;
- JSON GitHub invalide ;
- base SQLite inaccessible.

Les erreurs doivent être lisibles pour l'utilisateur.

Les exceptions internes ne doivent pas produire une stack trace énorme par défaut.

Prévoir un mode debug :

```bash
githor --debug scan
```

---

# 29. Logging

Utiliser le module standard `logging`.

Prévoir au minimum :

```text
INFO
WARNING
ERROR
DEBUG
```

La CLI doit rester lisible.

Les informations détaillées doivent être disponibles en mode debug.

---

# 30. Tests

Créer une vraie suite pytest.

Tester notamment :

## GitHub client

- authentification ;
- GET ;
- pagination ;
- erreurs ;
- rate limit.

## Collectors

- parsing repository ;
- parsing languages ;
- parsing structure ;
- activité.

## Storage

- création DB ;
- insertion repository ;
- insertion snapshot ;
- relations ;
- récupération.

## Findings

- README présent ;
- README absent ;
- CHANGELOG absent ;
- tests présents ;
- GitHub Actions présentes.

## Exporters

- JSON valide ;
- CSV valide ;
- Markdown valide.

Les appels GitHub doivent être mockés dans les tests.

**Les tests ne doivent jamais dépendre d'un token GitHub réel.**

---

# 31. Qualité du code

Respecter autant que raisonnablement possible :

- type hints ;
- fonctions courtes ;
- séparation des responsabilités ;
- docstrings sur les composants publics ;
- gestion explicite des erreurs ;
- pas de code mort ;
- pas de duplication inutile.

Ne pas surarchitecturer.

Je préfère une architecture simple et compréhensible à une architecture extrêmement abstraite.

---

# 32. Ce qui est explicitement hors périmètre V0.1

NE PAS implémenter maintenant :

- interface graphique ;
- dashboard web ;
- Ollama ;
- LLM ;
- RAG ;
- analyse AST ;
- analyse avancée de qualité du code ;
- SonarQube ;
- analyse avancée de sécurité ;
- modification de GitHub ;
- création d'issues ;
- création de PR ;
- modification de fichiers ;
- CI/CD du projet lui-même ;
- clone complet de tous les repositories ;
- analyse détaillée des dépendances.

Ces fonctionnalités seront étudiées dans les versions suivantes.

---

# 33. Roadmap prévue

L'architecture doit permettre d'évoluer vers :

## V0.2 — Code Auditor

Ajouter :

```text
clone local
AST
LOC
complexité
imports
structure réelle
tests
dépendances
```

## V0.3 — Project Intelligence

Ajouter :

```text
comparaison des projets
scores
détection des bonnes pratiques
détection des lacunes
historique des scores
```

## V0.4 — AI Advisor

Ajouter :

```text
Ollama
analyse du code
analyse documentation
analyse historique
recommandations
priorisation
```

À terme :

```text
metrics
+
findings
+
code
+
documentation
+
historique
        ↓
      Ollama
        ↓
recommendations
        ↓
improvement plan
```

---

# 34. Fonctionnalité future importante : comparaison inter-projets

Ne pas implémenter nécessairement dans V0.1, mais concevoir la base de données pour permettre :

```bash
githor compare
```

et obtenir plus tard :

```text
Project          Docs   Tests   CI   Security   Score

Astror            92     81    100      90       90
Architecturor     72     61    100      74       77
Sociologor        55     42     80      70       63
Psychologor       61     38     80      68       60
```

La comparaison inter-projets est une fonctionnalité importante du projet final.

---

# 35. Fonctionnalité future : conseiller de projets

L'objectif final est de pouvoir répondre à des questions comme :

```text
Quels sont mes projets les mieux documentés ?

Quels projets n'ont pas de tests ?

Quels projets n'ont pas été modifiés depuis 6 mois ?

Quelles bonnes pratiques sont présentes dans Astror
mais absentes de Sociologor ?

Quels éléments sont communs à mes PWA ?

Quelles améliorations reviennent le plus souvent ?

Quel projet devrait être amélioré en priorité ?
```

Le V0.1 doit donc conserver suffisamment de données historiques pour permettre ces analyses futures.

---

# 36. Principes importants

### Ne pas faire de magie

Les données doivent être traçables.

Un Finding doit pouvoir expliquer pourquoi il existe.

Exemple :

```text
missing_changelog
```

doit simplement correspondre à l'absence d'un fichier attendu.

### Ne pas utiliser l'IA prématurément

Le V0.1 doit être entièrement déterministe.

### Ne pas surcharger GitHub

Respecter pagination, rate limits et cache.

### Ne pas stocker de secrets

Aucun token dans SQLite, Git ou les exports.

### Préserver l'historique

Ne pas écraser les snapshots précédents.

---

# 37. Documentation du projet

Créer un `README.md` complet expliquant :

- objectif ;
- architecture ;
- installation sous Debian ;
- création du venv ;
- installation ;
- configuration du token ;
- première authentification ;
- première analyse ;
- commandes disponibles ;
- structure des données ;
- fonctionnement des snapshots ;
- export ;
- tests ;
- roadmap.

Exemple d'installation :

```bash
git clone <repository>
cd githor

python3 -m venv .venv
source .venv/bin/activate

pip install -e .
```

Puis :

```bash
export GITHUB_TOKEN="..."
githor auth check
githor scan
```

---

# 38. Critères d'acceptation du V0.1

Le V0.1 est considéré comme terminé lorsque je peux faire :

```bash
githor auth check
```

puis :

```bash
githor repos
```

puis :

```bash
githor scan
```

et obtenir une base SQLite contenant mes repositories et leurs snapshots.

Je dois ensuite pouvoir faire :

```bash
githor scan Architecturor
```

puis :

```bash
githor report Architecturor
```

et obtenir un rapport Markdown.

Je dois également pouvoir faire :

```bash
githor export --format json
githor export --format csv
```

et obtenir les exports correspondants.

Les tests doivent passer avec :

```bash
pytest
```

---

# 39. Méthode de développement demandée

Ne développe pas tout en une seule fois sans vérification.

Procède par étapes :

### Étape 1
Créer le projet Python et `pyproject.toml`.

### Étape 2
Créer la CLI minimale :

```bash
githor --help
```

### Étape 3
Créer la configuration.

### Étape 4
Créer `GitHubClient`.

### Étape 5
Implémenter :

```bash
githor auth check
```

### Étape 6
Implémenter la récupération des repositories.

### Étape 7
Créer SQLite + SQLAlchemy.

### Étape 8
Implémenter les snapshots.

### Étape 9
Ajouter languages / structure / activity.

### Étape 10
Ajouter Findings.

### Étape 11
Ajouter exports.

### Étape 12
Ajouter rapports Markdown.

### Étape 13
Ajouter tests complets.

Après chaque étape importante, vérifier que le projet fonctionne avant de continuer.

---

# 40. Résultat attendu

Je veux obtenir un **V0.1 réellement fonctionnel**, pas seulement un squelette.

À la fin, je dois pouvoir lancer :

```bash
githor scan
```

sur ma machine Debian et obtenir un inventaire persistant de mes repositories GitHub avec :

- métadonnées ;
- langages ;
- structure ;
- activité ;
- issues ;
- releases ;
- métriques de base ;
- Findings ;
- snapshots historiques ;
- exports JSON/CSV/Markdown ;
- rapports lisibles.

L'architecture doit ensuite pouvoir évoluer naturellement vers l'analyse locale du code puis l'analyse par LLM/Ollama.

**Commence par inspecter l'environnement de travail et le repository courant avant de créer ou modifier des fichiers.**

Si le projet existe déjà, respecte ce qui est présent et améliore-le plutôt que de repartir arbitrairement de zéro.

Avant toute modification importante, explique brièvement ce que tu vas faire, puis implémente-le.

À la fin de chaque étape, indique :

1. ce qui a été créé/modifié ;
2. comment le tester ;
3. les éventuels problèmes rencontrés ;
4. ce qui reste à faire.

Ne passe pas à une fonctionnalité hors périmètre V0.1 sans mon accord.