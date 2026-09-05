# Contexte du projet

Ce document explique **pourquoi** Githor est construit ainsi. Le [README](README.md)
dit ce que l'outil fait et comment s'en servir ; le [CHANGELOG](CHANGELOG.md) dit
ce qui a été fait et quand. Celui-ci s'adresse à qui reprend le travail — un
contributeur, ou moi-même dans six mois.

## Origine

77 dépôts personnels, tous nommés en `-or`, accumulés au fil des projets. Aucune
vue d'ensemble : impossible de dire lesquels ont des tests, lesquels n'ont pas
été touchés depuis un an, lesquels manquent d'une licence. Githor répond d'abord
à cette question-là — *où en sont mes projets ?* — avant toute ambition
d'analyse.

L'outil est conçu pour rester utile hors ligne, sur une machine Debian, sans
service tiers ni compte à créer.

## Ce que Githor est, et n'est pas

**Est** : un inventaire local, en lecture seule, qui interroge l'API GitHub,
normalise ce qu'elle renvoie, le range dans SQLite et en tire des constats
déterministes.

**N'est pas** :

- un outil qui écrit sur GitHub. Aucune écriture, jamais : ni issue, ni branche,
  ni fichier. La V0.1 n'utilise que des routes de lecture ;
- un service. Pas de serveur, pas de daemon, pas de compte ;
- un outil d'IA. La V0.1 est **entièrement déterministe**. L'IA arrive en V0.4,
  et seulement au-dessus de données déjà collectées et vérifiables.

## Décisions structurantes

Chacune a un coût ; il est assumé pour la raison indiquée.

### Le repository n'est pas son état

`repositories` décrit le projet ; `repository_snapshots` décrit ce qu'il était à
une date. Un scan **ajoute** un snapshot, il n'écrase jamais le précédent. C'est
ce qui rendra possibles la comparaison inter-projets (V0.3) et les questions du
type « quels projets se sont dégradés depuis six mois ? ».

Conséquence : la base grossit à chaque scan. C'est voulu.

### L'identité d'un dépôt est son identifiant GitHub

Jamais son nom. Un projet renommé reste le même projet, et son historique le
suit.

### Langages et fichiers pendent du snapshot ; commits, releases et issues du repository

Les premiers sont un **état** à une date : leur évolution doit rester lisible.
Les seconds sont des **faits datés** : ils ne se réécrivent pas d'un scan à
l'autre, ils s'ajoutent s'ils manquent. Des scans qui se recouvrent ne créent
donc aucun doublon.

### Tout est en UTC, à l'écriture comme à la lecture

SQLite ne conserve pas le fuseau. Sans le type `UTCDateTime`, une date consciente
écrite reviendrait naïve, et l'historique deviendrait faux selon la machine qui
le relit.

### Le jeton ne vit nulle part

Ni dans le dépôt, ni dans la configuration, ni dans SQLite, ni dans les exports.
Il est lu dans `GITHUB_TOKEN` ou, à défaut, obtenu de `gh auth token` à chaque
exécution. `githor config show` n'affiche que sa **provenance**.

> **Piège connu.** Ne pas exporter `GITHUB_TOKEN` durablement dans le shell : la
> variable prend le pas sur l'authentification de `gh`, et `git push` casse dès
> que le jeton exporté n'a pas les droits attendus. Githor sait aller chercher le
> jeton de `gh` tout seul ; c'est le mode normal.

### Un constat s'explique par un fait

`documentation.changelog` signifie exactement « aucun fichier CHANGELOG n'a été
trouvé dans l'arborescence relevée ». Le message cite le chemin qui l'a motivé,
jamais un simple booléen. Pas de score composite, pas d'heuristique floue : ce
qui n'est pas vérifiable à la main n'a pas sa place dans la V0.1.

### Les règles satisfaites sont enregistrées elles aussi

Statut `ok`, gravité `info`. La table `findings` porte donc, pour chaque
snapshot, l'état complet de ce qui a été vérifié — pas seulement ce qui
manquait. La synthèse ✓/✗ se reconstitue depuis la seule base, et l'on saura
plus tard **à quelle date** un projet a gagné son CHANGELOG.

### Ne pas surcharger GitHub

Six appels par dépôt, soit environ 470 requêtes pour 78 projets sur un quota
horaire de 5 000. L'arborescence est récupérée en **un seul** appel récursif ;
seule la fenêtre d'historique configurée est téléchargée. Un quota bas avertit,
un quota épuisé **arrête** — Githor n'attend jamais une heure en silence.

### Le cache, c'est la base elle-même

Deux exigences se contredisent : préserver l'historique demande un snapshot à
chaque scan, ménager le quota demande de ne pas remesurer ce qui vient de
l'être. Plutôt qu'un cache HTTP à faire vieillir en parallèle des données,
Githor interroge **la seule source qui sait déjà tout** : la table des snapshots.
Une requête, avant la boucle, donne la date de la dernière mesure de chaque
dépôt ; ceux qui ont moins de `snapshot_freshness_hours` sont écartés sans
qu'aucun appel ne parte.

La valeur par défaut est `0` — rien n'est dispensé. Le quota est un coût, la
perte d'historique une régression : le comportement par défaut doit protéger ce
qui ne se rattrape pas. C'est aussi pourquoi `--freshness` **remplace** la valeur
configurée au lieu de s'y ajouter, à l'inverse des options de périmètre :
`--freshness 0` doit toujours pouvoir forcer une photographie complète.

### Une valeur absente vaut mieux qu'un chiffre faux

Si la fenêtre téléchargée ne couvre pas 90 jours, le décompte à 90 jours est
`None`, pas zéro. Si GitHub tronque une arborescence, Githor l'annonce au lieu
de laisser croire à un décompte exact.

### Les métriques sont dérivées, jamais stockées

Fichiers, répertoires, fenêtres de commits, décomptes d'issues : tout se calcule
à la lecture, depuis les tables. Stocker un chiffre à côté de sa source, c'est
accepter qu'ils divergent un jour. Les fenêtres de commits se comptent depuis la
**date du snapshot**, non depuis l'instant de l'export : un même snapshot doit
toujours produire le même chiffre.

### Un export ne remplace pas le précédent

Le fichier produit est horodaté. Un export est une photographie, au même titre
qu'un snapshot ; deux exports successifs doivent pouvoir être comparés.

### Un rapport dit d'un dépôt ce que l'export dit de tous

`githor report` ne recalcule rien : il construit le même `RepositoryExport` que
l'export, et n'y ajoute que l'historique conservé. Deux chemins de lecture qui
compteraient chacun leurs fichiers finiraient par ne plus donner le même
nombre.

Ses vérifications ✓/✗ sont rendues depuis les **constats enregistrés**, non
depuis le catalogue courant : un rapport montre ce qui avait été vérifié à la
date du snapshot, et non ce que Githor saurait vérifier aujourd'hui.

### Ce qui est produit va sur `stdout`, ce qui commente va sur `stderr`

Un rapport sans `--output` est écrit tel quel, sans habillage ni repli : Rich
casserait ses tableaux dès que le terminal est étroit, et une redirection ne
doit jamais produire autre chose que le fichier attendu.

## État d'avancement

La **0.1.0** est publiée : les treize étapes du plan sont franchies et le cahier
des charges n'a plus de manque connu. Le détail est dans le
[CHANGELOG](CHANGELOG.md) ; la suite est la V0.2, décrite plus bas.

## Conventions

- **Français** pour les docstrings, les commentaires, les messages utilisateur et
  les commits ; **anglais** pour les identifiants du code et le schéma de base.
- **Un commit par étape**, dont le message dit *pourquoi*, pas seulement *quoi*.
- `ruff format`, `ruff check` et `mypy` doivent passer sans avertissement ;
  `mypy` est configuré en mode strict sur `src/`.
- Docstrings sur tout composant public, type hints partout, fonctions courtes.
- **Les tests ne dépendent jamais d'un jeton réel** : le transport HTTP est
  mocké, `gh` est neutralisé par une fixture, et la configuration de la machine
  hôte est isolée. Un test qui joindrait le réseau est un bug.

## Absence de migrations

Le schéma est créé par `Base.metadata.create_all`, qui ajoute les tables
manquantes mais **jamais** une colonne à une table existante. Tant qu'aucun outil
de migration n'est en place, toute évolution du schéma casse silencieusement les
bases déjà créées.

C'est pourquoi la table `findings` n'a pas de colonne `evidence` : le chemin qui
motive un constat est repris dans son message. Si une colonne devient
indispensable, il faudra d'abord introduire les migrations.

## Trajectoire

| Version | Contenu | Ce que la V0.1 doit préparer |
|---|---|---|
| **V0.1** | Inventaire, snapshots, findings, exports, rapports | — |
| V0.2 | Code Auditor : clone local, AST, LOC, complexité, dépendances | des modèles indépendants de la forme des réponses GitHub |
| V0.3 | Project Intelligence : comparaison, scores, historique | assez d'historique pour comparer deux dates |
| V0.4 | AI Advisor : analyse via Ollama, recommandations priorisées | des findings traçables, que l'IA commente sans les inventer |

Les couches sont séparées pour cela : `github/` ne connaît ni la base ni la CLI,
les `collectors/` font le pont vers les modèles normalisés, les `rules/` ne
connaissent ni GitHub ni SQLite. Une analyse locale du code (V0.2) s'ajoutera
comme une nouvelle source alimentant les mêmes modèles, sans réécrire l'existant.

## Spécification d'origine

Le cahier des charges initial est conservé dans le dépôt
([CAHIER-DES-CHARGES.md](CAHIER-DES-CHARGES.md)). Il reste la référence de ce que
la V0.1 doit livrer, y compris ce qui n'est pas encore fait. Toute divergence
entre ce document et le code est soit un manque à combler, soit une décision à
consigner ici.
