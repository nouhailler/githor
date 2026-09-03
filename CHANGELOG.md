# Changelog

Toutes les évolutions notables de Githor sont consignées ici.

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/), et le
projet respecte le [versionnement sémantique](https://semver.org/lang/fr/).

Les étapes numérotées renvoient au plan de développement de la V0.1 : dix des
treize étapes sont franchies.

## [Non publié]

Ce qui reste avant de figer la **0.1.0** :

- collecte des issues et des releases (leurs tables existent, elles sont vides) ;
- exports JSON, CSV et Markdown — `githor export` (étape 11) ;
- rapports lisibles par repository — `githor report` (étape 12) ;
- complétion de la suite de tests (étape 13).

### Ajouté

- **Moteur de règles et findings** *(étape 10)*.
  - modèle `Finding` — catégorie, règle, gravité, statut, message, recommandation ;
  - catalogue de dix règles déterministes couvrant la documentation
    (`readme`, `license`, `changelog`, `contributing`, `docs`), le développement
    (`tests`, `github_actions`), l'infrastructure (`docker`, `dependabot`) et la
    maintenance (`activity`) ;
  - `githor findings` — synthèse « ce qui est là / ce qui manque » de tous les
    dépôts, ou détail d'un seul. La commande relit la base : elle ne joint
    jamais GitHub ;
  - les règles satisfaites sont enregistrées elles aussi (statut `ok`, gravité
    `info`) : la table `findings` porte l'état complet de ce qui a été vérifié à
    chaque snapshot ;
  - une règle s'ajoute par une entrée du catalogue, jamais dans la CLI.
- **Collecte des langages, de l'arborescence et de l'activité** *(étape 9)*.
  - répartition des langages, en octets bruts et en pourcentage ;
  - arborescence complète en un seul appel récursif, avec détection déclarative
    des fichiers et répertoires attendus ;
  - activité limitée à la fenêtre configurée (`commit_history_days`), décomptes
    sur 30 et 90 jours produits seulement si la fenêtre les couvre.
- **Snapshots persistés et commande `scan`** *(étape 8)*. Chaque scan **ajoute**
  une mesure datée ; les précédentes restent intactes. `githor scan NOM` scanne
  un seul dépôt.
- **Schéma SQLite et couche de persistance** *(étape 7)*. Huit tables, clés
  étrangères réellement appliquées (`PRAGMA foreign_keys = ON` sur chaque
  connexion), dates stockées et relues en UTC. `githor db init` est idempotent.
- **Inventaire des repositories et commande `repos`** *(étape 6)*. Forks et
  dépôts archivés exclus par défaut, exclusions toujours annoncées.
- **Commande `auth check` et résolution du jeton** *(étape 5)*. Le jeton vient de
  `GITHUB_TOKEN` ou, à défaut, de `gh auth token` ; il n'est jamais affiché ni
  stocké.
- **Client HTTP GitHub** *(étape 4)*. Pagination guidée par l'en-tête `Link`,
  tentatives bornées avec attente exponentielle, surveillance du quota, erreurs
  nommées dérivant de `GithorError`.
- **Configuration TOML validée et commande `config show`** *(étape 3)*. La
  configuration ne contient jamais de secret.
- **Socle du projet et CLI minimale** *(étapes 1 et 2)*. `pyproject.toml`,
  arborescence en couches, `githor --help`, logging sur `stderr`.
