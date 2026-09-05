# Changelog

Toutes les évolutions notables de Githor sont consignées ici.

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/), et le
projet respecte le [versionnement sémantique](https://semver.org/lang/fr/).

Les étapes numérotées renvoient au plan de développement de la V0.1 : les
treize sont franchies.

## [Non publié]

La V0.1 remplit ses critères d'acceptation et le cahier des charges n'a plus de
manque connu. Reste à figer la **0.1.0**.

### Ajouté

- **Fraîcheur des snapshots** *(§27 du cahier des charges)*.
  - avant d'interroger GitHub, le scan relit **en une requête** la date du
    dernier snapshot de chaque dépôt et écarte ceux mesurés depuis moins de
    `scan.snapshot_freshness_hours`. Un dépôt ignoré ne coûte rien : ni appel,
    ni snapshot, ni écriture. Seul le listage des dépôts subsiste ;
  - le cache logique, c'est **la base elle-même** : la table des snapshots sait
    déjà quand chaque mesure a eu lieu, rien n'est à faire vieillir en parallèle ;
  - la valeur par défaut, `0`, ne dispense de rien — un scan mesure tout, comme
    auparavant. Le quota est un coût, la perte d'historique une régression : le
    comportement par défaut protège ce qui ne se rattrape pas ;
  - `githor scan --freshness HEURES` **remplace** la valeur configurée au lieu de
    s'y ajouter, à l'inverse des options de périmètre : `--freshness 0` force une
    photographie complète malgré une configuration plus permissive ;
  - un dépôt jamais mesuré est toujours scanné, et chaque dépôt écarté est
    affiché avec l'âge de sa dernière mesure — la décision reste visible.
- **Suite de tests complète** *(étape 13)*. 335 tests couvrent le client GitHub
  (authentification, `GET`, pagination, erreurs, quota), les collectors, le
  stockage, les règles, les exports, les rapports et la CLI de bout en bout.
  Aucun ne joint le réseau ni ne dépend d'un jeton réel.
- **Rapports Markdown individuels** *(étape 12)*.
  - `githor report NOM` produit le rapport d'un seul dépôt : mesures, langages,
    ce qui a été vérifié, ce qui manque, releases, et depuis quand le dépôt est
    suivi. Comme l'export, il relit la base et ne joint jamais GitHub ;
  - le rapport est construit sur le **même jeu de données** que l'export
    (`build_repository_export`) : un dépôt y est décrit exactement comme dans
    l'inventaire, et les deux ne peuvent pas diverger ;
  - les vérifications ✓/✗ sont rendues depuis les **constats enregistrés**, non
    depuis le catalogue courant : un rapport montre ce qui avait été vérifié à
    la date du snapshot ;
  - sans `--output`, le Markdown part tel quel sur `stdout` — ni habillé ni
    replié, pour qu'un tableau survive à un terminal étroit comme à un tube.
    Avec un répertoire existant en `--output`, le fichier est horodaté et
    n'écrase pas le précédent ;
  - les fragments de rendu Markdown communs à l'export et au rapport (dates,
    gravités, cases neutralisées) sont réunis dans `utils/markdown.py`, pour
    qu'ils ne divergent pas.
- **Releases, issues et exports** *(étape 11)*.
  - collecte des releases (tag, nom, date, brouillon, préversion) et des issues
    (identifiant, numéro, titre, état, dates). Les unes et les autres sont
    **mises à jour** d'un scan à l'autre : un brouillon finit par être publié,
    une issue par se fermer ;
  - les pull requests, que GitHub range parmi les issues, sont écartées du
    stockage et seulement comptées — le compteur `open_issues` de GitHub les
    inclut à tort. Le snapshot porte désormais `open_prs` ;
  - première couche de **métriques**, dérivée des tables et jamais stockée en
    double : fichiers, répertoires, langages, fenêtres de commits comptées depuis
    la date du snapshot, releases, issues ouvertes et fermées, constats par
    gravité ;
  - `githor export --format json|csv|markdown` écrit dans `data/exports/` un
    fichier horodaté, qui n'écrase jamais le précédent. L'export relit la base :
    il ne joint pas GitHub, et ne dépend donc ni du réseau ni du quota.
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
