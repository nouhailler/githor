# Changelog

Toutes les évolutions notables de Githor sont consignées ici.

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/), et le
projet respecte le [versionnement sémantique](https://semver.org/lang/fr/).

Les étapes numérotées renvoient au plan de développement : les treize de la V0.1
sont franchies, les étapes 14 à 18 constituent la V0.2, les étapes 19 à 23 la
V0.3, les étapes 24 à 28 la V0.4, et les étapes 29 à 32 la 0.5.0.

## [0.5.0] — 2026-09-08

**Conseiller de projets multi-dépôts.** La 0.4.0 conseillait un dépôt ; la
0.5.0 répond à des questions sur l'ensemble du parc, en langage naturel —
le §35 du cahier des charges, volontairement laissé de côté lors de la V0.4.

### Ajouté

- **Réponses en texte libre dans le client Ollama** *(étape 29)*.
  - `OllamaClient.generate` gagne un paramètre `format` optionnel
    (`"json"` par défaut, comme avant ; `None` pour l'omettre) — une réponse
    en prose libre n'a pas à être contrainte au JSON, contrairement à ce que
    demande `githor advise`. `githor advise` ne change pas de comportement.
- **Résumé du parc et génération de la réponse** *(étape 30)*,
  `githor.analysis.portfolio_advisor`.
  - chaque dépôt enregistré est résumé en une ligne (langage, score, constats
    ouverts, présence de tests locaux, dernière activité) depuis le même
    `Dataset` que `githor export`/`compare` ; aucune donnée nouvelle,
    aucun nouvel appel ;
  - la question posée et le résumé complet du parc sont donnés à Ollama en
    une seule fois, avec instruction explicite de ne rien affirmer qui n'y
    figure pas et de citer les dépôts par leur nom complet ;
  - à l'échelle d'une centaine de dépôts, le résumé tient en quelques
    kilo-octets : aucune recherche ni indexation préalable (RAG) n'a été
    nécessaire pour cette version.
- **Commande `githor ask`** *(étape 31)*.
  - `githor ask "question"` répond sur l'ensemble des dépôts enregistrés ;
    `--model` remplace ponctuellement celui configuré ;
  - rien n'est stocké : contrairement à `githor advise`, poser une question
    ne laisse aucune trace en base — c'est un outil de consultation, pas une
    mesure à préserver ;
  - la réponse est systématiquement accompagnée d'un rappel qu'elle est
    générée : contrairement à un score ou un constat, une réponse en prose
    libre n'est pas structurellement vérifiable par Githor lui-même ;
  - ne joint jamais GitHub : comme `report`, `findings`, `compare` et
    `advise`, elle ne relit que la base.

## [0.4.0] — 2026-09-08

**AI Advisor.** La 0.3.0 savait comparer les dépôts sur un score ; la 0.4.0
les conseille. Githor priorise, Ollama rédige — la première fois que l'IA
entre dans le projet, et seulement au-dessus de données déjà collectées et
vérifiables, jamais à leur place.

### Ajouté

- **Client Ollama et configuration** *(étape 24)*, `githor.ollama`.
  - accès à un serveur Ollama **local uniquement** : aucune authentification,
    aucun quota, aucune donnée du dépôt envoyée à un service tiers — décision
    prise en amont face à l'alternative d'une API cloud (OpenRouter), rejetée
    précisément parce qu'elle aurait exporté le code hors de la machine ;
  - timeout de connexion court (le serveur est local : soit il répond, soit
    il ne tourne pas) et timeout total long et configurable, l'inférence
    locale pouvant prendre plusieurs minutes ;
  - erreurs actionnables : serveur injoignable renvoie la commande pour le
    démarrer (`ollama serve`), modèle absent renvoie celle pour le récupérer
    (`ollama pull <modèle>`) ;
  - nouvelle section `[ollama]` dans la configuration (`host`, `model`,
    `timeout_seconds`), sur le modèle exact des sections existantes.
- **Priorisation déterministe et génération des recommandations**
  *(étape 25)*, `githor.analysis.advisor`.
  - **Githor priorise, Ollama rédige.** L'ordre des recommandations vient des
    constats **ouverts** déjà enregistrés, triés par gravité — le même tri
    que les rapports et les exports. Ollama ne choisit jamais quoi mettre en
    premier, et ne peut mentionner aucun fait absent de cette liste ;
  - le score (V0.3) et les métriques de code (V0.2) sont donnés à Ollama en
    **contexte** seulement : ils aident à mieux rédiger, sans créer de
    recommandation supplémentaire ;
  - la réponse d'Ollama est contrainte au format JSON et associée aux
    constats un à un ; une réponse non exploitable **dégrade** proprement —
    le texte brut est conservé, marqué comme tel, plutôt que de faire
    échouer le dépôt ou d'inventer une association ;
  - un dépôt sans constat ouvert n'est pas conseillé : « rien à recommander »,
    pas un résultat vide.
- **Persistance des recommandations** *(étape 26)*.
  - deux tables neuves, `advice_runs` et `advice_items`, **aucune colonne
    ajoutée ailleurs** — le même geste que les six tables de `code_audits` en
    V0.2 ;
  - contrairement au score, dérivé à la volée et jamais stocké, le texte
    produit par Ollama **est** persisté : il coûte un appel au modèle et
    n'est pas reproductible à l'identique d'un appel à l'autre. C'est
    l'exception qui confirme la règle posée en V0.3 — voir CONTEXT.md ;
  - chaque exécution **ajoute** une entrée, comme un scan ajoute un snapshot :
    elle n'écrase jamais la précédente.
- **Commande `githor advise`** *(étape 27)*.
  - conseille un dépôt ou tous ceux de la base, `--save/--no-save`, `--model`
    pour remplacer ponctuellement celui configuré ;
  - échec isolé par dépôt (serveur injoignable, modèle absent, délai dépassé) :
    les autres dépôts continuent d'être traités, comme pour `githor audit` ;
  - ne joint jamais GitHub : comme `report`, `findings` et `compare`, elle ne
    relit que la base.

### Documenté

- `CONTEXT.md` documente l'exception : les recommandations sont stockées,
  contrairement aux scores, parce qu'elles ne sont pas dérivables. Deux
  tables neuves suffisent, aucune migration n'a été nécessaire.

## [0.3.0] — 2026-09-07

**Project Intelligence.** La 0.1.0 disait ce que GitHub sait d'un dépôt, la
0.2.0 a ouvert son code. La 0.3.0 compare : un score par dépôt, dérivé de ce
qui était déjà enregistré, et rien de plus stocké pour l'obtenir.

### Ajouté

- **Catégorie `security` et ses règles** *(étape 19)*.
  - `infrastructure.dependabot` devient `security.dependabot` — les constats
    déjà enregistrés sous l'ancien identifiant restent en base, inchangés :
    un rapport tiré d'un snapshot antérieur à ce changement continue de les
    citer tels quels ;
  - nouvelle règle `security.policy` : présence d'un `SECURITY.md`. Le
    marqueur existait déjà dans le collecteur de structure depuis la 0.1.0,
    inutilisé par aucune règle — coût nul côté collecte.
- **Score dérivé des findings** *(étape 20)*, `githor.scoring`.
  - un score est un pourcentage de règles satisfaites, par groupe (Docs,
    Tests, CI, Security) et global ; un groupe sans règle représentée dans
    les findings vaut `None`, jamais zéro — une valeur absente vaut mieux
    qu'un chiffre faux ;
  - **rien n'est stocké.** `findings` porte déjà, pour chaque snapshot, le
    statut de chaque règle : un score n'est qu'une lecture groupée de ce qui
    existe. Aucune table, aucune colonne, aucune migration.
- **Score dans les exports et les rapports** *(étape 21)*.
  - `RepositoryExport` gagne un champ `score`, calculé une fois dans
    `build_repository_export` : `githor compare`, `githor report` et les
    trois formats d'export le lisent tous depuis la même fonction ;
  - `githor report` gagne une section **Score** et, dès le second snapshot,
    une section **Évolution du score** — l'historique existe déjà dans les
    findings de chaque snapshot passé, il suffit de les rejouer ;
  - le CSV gagne cinq colonnes `score_*`, vides et non à zéro pour un dépôt
    jamais scanné ; le Markdown multi-dépôts gagne une colonne Score.
- **Commande `githor compare`** *(étape 22)*.
  - tableau Project/Docs/Tests/CI/Security/Score, trié du meilleur score au
    plus faible — le tableau visé par le cahier des charges (§34) ;
  - `--format json|csv` réutilise les mêmes rendus que `githor export`, sur
    `stdout` plutôt que dans un fichier : `compare` est une lecture, pas une
    photographie ;
  - ne joint jamais GitHub : comme `findings` et `report`, la commande relit
    la seule base.

### Documenté

- `CONTEXT.md` révise sa propre prédiction : la V0.3 n'a pas eu besoin des
  migrations qu'elle anticipait, le score se dérivant entièrement des
  findings déjà persistés.

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
