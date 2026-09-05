# Changelog

Toutes les évolutions notables de Githor sont consignées ici.

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/), et le
projet respecte le [versionnement sémantique](https://semver.org/lang/fr/).

Les étapes numérotées renvoient au plan de développement : les treize de la V0.1
sont franchies, et les étapes 14 à 18 constituent la V0.2.

## [0.2.0] — 2026-09-05

**Code Auditor.** La 0.1.0 regardait un dépôt de l'extérieur — ce que GitHub en
dit, quels fichiers y sont présents. La 0.2.0 l'ouvre : elle clone le code
localement et le lit.

### Ajouté

- **Miroir local des dépôts** *(étape 14)*.
  - `githor mirror` clone ou met à jour la copie locale des dépôts enregistrés,
    superficiellement et sur la seule branche par défaut : l'analyse porte sur
    l'état du code, jamais sur son passé ;
  - la commande relit la **base**, pas l'API : aucun quota n'est consommé ;
  - **le jeton ne passe pas par l'URL de clone.** L'y coudre l'écrirait en clair
    dans le `.git/config` du miroir, où il survivrait à l'exécution. Les dépôts
    privés s'authentifient par le gestionnaire d'identifiants habituel de `git`,
    et `GIT_TERMINAL_PROMPT=0` garantit qu'un dépôt inaccessible échoue tout de
    suite au lieu d'attendre une saisie ;
  - **un répertoire que Githor n'a pas cloné n'est jamais touché** : l'`origin`
    est comparé à l'URL attendue avant toute mise à jour, et un nom de dépôt qui
    ne peut pas devenir un chemin sûr est refusé. `reset --hard` détruit du
    travail ;
  - le miroir est ramené à l'état publié à chaque passage : une analyse décrit
    le dépôt distant, non les résidus de la précédente ;
  - `--offline` lit les miroirs déjà présents sans interroger l'origine.
- **Analyse du code : lignes, structure, complexité, imports** *(étape 15)*.
  - `githor audit` compte les lignes — code, commentaire, vide — de tous les
    langages reconnus, une soixantaine d'extensions ;
  - structure, complexité cyclomatique et imports viennent de l'**AST de
    l'interpréteur** et ne sont établis que pour Python : compter les fonctions
    d'un TypeScript à l'expression régulière produirait un chiffre
    invérifiable, et une valeur absente vaut mieux qu'un chiffre faux ;
  - une **docstring est du code**, non un commentaire : elle est évaluée,
    attachée à l'objet et lisible à l'exécution ;
  - la complexité d'une **fonction imbriquée** lui est attribuée en propre et
    n'entre pas dans celle qui la contient, faute de quoi la même branche serait
    comptée deux fois ;
  - un import est **local** quand sa racine est un paquet du dépôt, disposition
    `src/` comprise — sans quoi tout projet moderne verrait ses propres modules
    comptés comme tierce partie ;
  - rien n'est écarté en silence : répertoires d'artefacts, fichiers binaires,
    fichiers trop gros et modules non parsables sont comptés et rapportés. Les
    liens symboliques ne sont jamais suivis.
- **Dépendances déclarées et détection des tests** *(étape 16)*.
  - lecture des manifestes : `pyproject.toml` (PEP 621, PEP 735, Poetry),
    `requirements*.txt`, `setup.cfg`, `package.json`, `Cargo.toml`, `go.mod` ;
  - ce sont les dépendances **déclarées**, non celles installées ni celles
    importées : garder les trois distinctes rend leurs écarts lisibles. Chaque
    dépendance cite le manifeste qui l'a déclarée ;
  - le rapprochement entre imports tiers et déclarations est une **piste**,
    jamais un manquement — un paquet s'installe souvent sous un autre nom que
    celui sous lequel il s'importe. Aucune règle n'en tire de constat ;
  - détection **structurelle** des tests : conventions de nommage pour les
    fichiers, quel que soit le langage, et AST pour compter les fonctions
    Python. Rien n'est exécuté — Githor ne lance jamais les tests d'un dépôt
    qu'il analyse ;
  - les cadres de test sont reconnus par import *et* par dépendance déclarée :
    l'import prouve un usage, la dépendance couvre ceux qui s'invoquent par
    leur runner sans jamais s'importer.
- **Persistance des audits et section « Code » des rapports** *(étape 17)*.
  - chaque `githor audit` **ajoute** un audit, comme un scan ajoute un
    snapshot : deux analyses successives se comparent, elles ne se remplacent
    pas ;
  - **six tables neuves, aucune colonne ajoutée à une table existante.** C'est
    la seule évolution que `create_all` sache appliquer à une base déjà créée,
    donc la seule possible tant qu'aucun outil de migration n'est en place :
    les bases de la 0.1.0 s'ouvrent sans rien perdre ;
  - l'audit pend du **repository** et non du snapshot : il se lit sur un clone
    local, et l'y rattacher obligerait à scanner avant d'analyser. Un rapport
    rend donc la section Code même pour un dépôt jamais scanné ;
  - seuls des **faits** sont écrits — un module, une fonction, une classe, un
    import, une dépendance. Toutes les métriques se recalculent à la lecture,
    hormis les fichiers binaires et trop gros, qui n'ont pas de ligne à eux et
    ne pourraient pas être recomptés ;
  - `--no-save` regarde sans écrire.
- **L'analyse locale dans les exports** *(étape 18)*.
  - le JSON porte un objet `code` complet, `null` pour un dépôt jamais analysé ;
  - le CSV gagne treize colonnes `code_*`, **vides** et non à zéro quand
    l'analyse n'a pas eu lieu : un zéro laisserait croire à une mesure faite ;
  - l'export Markdown résume l'analyse en une ligne par dépôt, le détail
    restant au rapport individuel ;
  - export et rapport tirent leurs chiffres de la même fonction : ils ne
    peuvent pas donner deux valeurs différentes du même dépôt.

### Corrigé

- la fixture de tests qui neutralisait `gh` remplaçait `subprocess.run` tout
  entier, ce qui coupait aussi `git`. Elle ne neutralise plus que `gh` ; la
  suite ne joint pas plus le réseau qu'avant.

## [0.1.0] — 2026-09-05

Première version publiée. Githor liste les dépôts d'un compte, en prend des
snapshots datés, en tire des constats déterministes, et sait les exporter comme
les rapporter — le tout en lecture seule, hors ligne une fois la collecte faite,
et sans jamais écrire sur GitHub. Les critères d'acceptation du cahier des
charges sont remplis, et celui-ci n'a plus de manque connu.

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
