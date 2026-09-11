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
normalise ce qu'elle renvoie, le range dans SQLite, lit le code sur un clone
local et en tire des constats déterministes.

**N'est pas** :

- un outil qui écrit sur GitHub. Aucune écriture, jamais : ni issue, ni branche,
  ni fichier. Les routes employées sont toutes en lecture, et le clone local ne
  connaît ni `push`, ni tag, ni création de branche ;
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

### Un signal de contenu, une exception bornée au marqueur

Jusqu'à la 0.8.0, un marqueur ne regardait qu'un **nom** de fichier ou de
répertoire, jamais son contenu — le module `collectors/structure.py` l'a
toujours dit explicitement. Ce choix tenait tant que chaque constat pouvait
se réduire à « ce fichier existe-t-il ? ».

Il a cessé de suffire avec les contrôles de conformité éditoriale des sites
publiés par l'utilisateur : une mention légale, une section « à propos » ou
un lien vers son propre site (`swinux.ch`) ne portent pas un nom de fichier
prévisible, seulement du texte — et sa mise à jour automatique se reconnaît
à une **dépendance** (`vite-plugin-pwa`, repérée à titre d'exemple dans
Astror — un dépôt parmi d'autres, pas une référence), pas à un fichier au
nom particulier à ce seul projet. Githor lit donc, pour la première fois,
le **contenu** de quelques fichiers.

**Ce qui reste borné, par choix** : seul un ensemble fixe et court de
fichiers déjà repérés par marqueur est récupéré par dépôt — le README, une
page légale/« à propos » candidate, la page d'accueil, `package.json` — au
plus cinq appels, jamais un scan de l'arborescence entière. La fonction qui
porte cette exception, `githor.collectors.content.collect_content_signals`,
documente ce périmètre dans son propre docstring, pour qu'il reste visible
sans revenir ici.

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

### Le score aussi est dérivé — et son historique avec lui

La V0.3 (`githor compare`) avait besoin d'un score par catégorie (Docs, Tests,
CI, Security) et de son évolution dans le temps. Rien n'a été ajouté au
schéma : `findings` porte déjà, pour chaque snapshot, le statut de chaque
règle. Un score n'est donc qu'une lecture groupée de ce qui existe — voir
`githor.scoring.compute_score` — et l'historique des scores n'est que ce même
calcul rejoué sur les findings de chaque snapshot passé.

Cela révise ce que ce document prédisait plus bas : la V0.3 n'a **pas**
obligé à introduire les migrations. Une table `repository_scores` aurait
dupliqué une information déjà entièrement reconstituible, en violation directe
du principe ci-dessus. Le jour où un score deviendra coûteux à recalculer, ou
où la comparaison portera sur des critères qui ne se lisent plus dans
`findings`, la question se reposera — mais elle ne s'est pas posée ici.

### Les recommandations, elles, sont stockées — jamais dérivées

Exception délibérée au principe qui précède. `githor advise` (V0.4) ajoute
deux tables neuves, `advice_runs` et `advice_items`, qui persistent le texte
produit par Ollama. La différence avec un score tient en une phrase : un
score se **recalcule** à l'identique depuis les findings, une recommandation
ne le peut pas — elle coûte un appel à un modèle de langage, et deux appels
avec le même prompt ne produisent pas forcément la même formulation. Stocker
ce texte, c'est faire pour Ollama ce que `code_audits` fait déjà pour une
analyse de code coûteuse : conserver le résultat d'une mesure qu'on ne peut
pas se permettre de refaire à chaque lecture.

Ce que la V0.4 ne stocke en revanche jamais, c'est l'**ordre** des
recommandations : il reste calculé par Githor, depuis les mêmes findings
triés par gravité qu'utilisent déjà les rapports et les exports
(`SEVERITY_ORDER`). Ollama rédige, il ne priorise pas — c'est la
traduction directe de « ne pas faire de magie » appliquée à l'IA : chaque
recommandation reste rattachée à la règle qui l'a motivée
(`AdviceItemRow.source_rule`), jamais un jugement sans source.

Deux tables neuves, aucune colonne touchée ailleurs : comme pour la V0.2 et
la V0.3, aucune migration n'a été nécessaire ici non plus.

### Le conseiller de parc envoie tout, plutôt que de chercher

`githor ask` (§35) répond à une question libre sur l'ensemble des dépôts en
donnant à Ollama un résumé de **chacun** des ~80 dépôts enregistrés, en une
seule fois — jamais une recherche préalable qui ne remonterait que les
dépôts jugés pertinents (RAG). À cette échelle, tout le résumé tient en
quelques kilo-octets, largement dans la fenêtre de contexte d'un modèle
local ; ajouter une étape de recherche aurait été de la complexité sans
bénéfice mesurable.

Cela ne tiendra pas indéfiniment : un parc dix fois plus gros dépassera la
fenêtre de contexte utile, et la question d'indexer/filtrer avant d'envoyer
se reposera alors. Ce n'est pas le cas aujourd'hui, et anticiper cette limite
maintenant aurait été de la complexité sans bénéfice mesurable — la même
logique que celle qui a écarté les migrations tant qu'aucune colonne n'en a
eu besoin.

Contrairement à `githor advise`, la réponse produite ici n'a pas de structure
de sortie à valider : une question libre ne se laisse pas découper en
éléments associés un par un. Githor ne peut donc pas vérifier la réponse
elle-même — seulement garantir que les faits qu'elle *peut* citer sont
réels. C'est pourquoi la commande rappelle systématiquement, dans son
affichage, que la réponse est générée et reste à vérifier.

### L'interface graphique : une TUI d'abord, un choix révisé ensuite

`githor tui` (0.6.0) a d'abord répondu au besoin d'une interface plus
confortable que la CLI sans rouvrir la décision prise à l'origine : « pas de
service, pas de serveur, pas de daemon ». Une TUI [Textual](https://textual.textualize.io/)
tient dans le même unique processus que le reste de Githor, ne joint aucun
réseau de plus que ce qu'il joint déjà, et réutilise Rich.

**À l'usage, elle a été jugée trop pauvre visuellement**, et portait un bug
réel : le focus tombait par défaut dans le champ de filtre, qui capture
toutes les touches imprimables — `q` pour quitter s'y tapait au lieu de
déclencher le raccourci, sans indication visible du problème. Plutôt que de
rapiécer, l'utilisateur a choisi de basculer sur une **interface web locale**
(0.7.0, `githor web`), acceptant cette fois l'écart avec le principe
d'origine — une TUI n'a pas suffi à l'éviter non plus, et le HTML/CSS donne
un contrôle visuel qu'un terminal ne permet pas.

Ce qui reste du principe d'origine, par choix, malgré le serveur : il
n'écoute que sur `127.0.0.1` par défaut ([config.py](src/githor/config.py) —
jamais exposé sur le réseau), aucune ressource (CSS, JS) n'est chargée depuis
un CDN, pour que l'interface reste utilisable hors ligne, et la commande
bloque le terminal tant qu'elle tourne, à l'arrêter par `Ctrl+C` — le même
modèle mental qu'un `python -m http.server`, rien de caché en arrière-plan.

**La TUI reste disponible** (`githor tui`, dépendance `textual`) : rien n'a
été supprimé, elle n'est simplement plus mise en avant dans la documentation.

Les deux interfaces partagent la même contrainte, pour la même raison que la
première fois : cette version reste **volontairement en lecture seule**. De
fond, `githor web` ne fait pas exception au reste du projet — elle ne
connaît que `config`, `storage`, `exporters` et `reports`, jamais `cli.py`,
qui l'invoque en sens inverse ; le détail d'un dépôt construit **exactement**
le même `Report` que `githor report`/`githor tui`, simplement rendu en
HTML/CSS plutôt qu'en Markdown ou en widgets terminal — le même geste que la
coexistence déjà en place entre les renderers JSON/CSV/Markdown des exports.
De circonstance : juger vite un résultat concret avant d'investir dans des
actions qui demanderaient de gérer des tâches longues en arrière-plan sans
bloquer la page.

### Un export ne remplace pas le précédent

Le fichier produit est horodaté. Un export est une photographie, au même titre
qu'un snapshot ; deux exports successifs doivent pouvoir être comparés.

### L'analyse locale est une seconde source, pas une extension de la première

`github/` lit une API ; `vcs/` et `analysis/` lisent des fichiers sur disque.
Les deux chemins ignorent tout l'un de l'autre et alimentent les mêmes modèles.
C'est ce qui a permis d'ajouter la V0.2 sans rien réécrire de la V0.1 — et ce
qui permettra à la V0.4 de commenter les deux sans les confondre.

Conséquence assumée : un dépôt peut avoir un snapshot sans audit, ou l'inverse.
Un rapport dit ce qui existe, sans jamais supposer l'autre.

### L'audit pend du repository, non du snapshot

Un audit se lit sur un clone local et n'exige aucun appel à GitHub. Le rattacher
au snapshot obligerait à scanner avant d'analyser, alors que rien ne le demande.

### Un clone est une copie jetable, jamais un dépôt de travail

Le miroir est ramené à l'état publié à chaque passage : une analyse doit décrire
le dépôt distant, non les résidus de la précédente. C'est aussi pourquoi Githor
vérifie l'`origin` avant d'agir et refuse de toucher un répertoire qu'il n'a pas
cloné — `reset --hard` détruit du travail.

Et c'est pourquoi le jeton ne passe pas par l'URL de clone : l'y coudre
l'écrirait en clair dans le `.git/config` du miroir, où il survivrait à
l'exécution. L'authentification des dépôts privés revient à `git`.

### L'analyse profonde s'arrête à Python

Les lignes sont comptées pour tout langage reconnu ; la structure, la complexité
et les imports ne le sont que pour Python, où ils viennent de l'AST de
l'interpréteur. Une heuristique par expressions régulières sur du TypeScript
produirait un chiffre qu'on ne saurait pas justifier.

Deux corollaires, notés ici parce qu'ils surprennent :

- une **docstring est du code**, non un commentaire. Elle est évaluée, attachée
  à l'objet et lisible à l'exécution ; la compter autrement flatterait la part
  commentée ;
- la complexité d'une **fonction imbriquée** lui est attribuée en propre et
  n'entre pas dans celle qui la contient, faute de quoi la même branche serait
  comptée deux fois.

### Déclaré, installé, importé sont trois choses

Githor lit ce que les manifestes **déclarent**. Il ne sait rien de ce qui est
installé, et il relève à part ce qui est **importé**. Confondre les trois
donnerait un chiffre commode et faux ; les garder distinctes rend leurs écarts
lisibles.

Le rapprochement entre imports et déclarations est donc une piste, jamais un
constat : un paquet s'installe souvent sous un autre nom que celui sous lequel
il s'importe. Aucune règle n'en tire de finding.

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
des charges n'a plus de manque connu.

La **V0.2 — Code Auditor** est livrée (étapes 14 à 18) : clone local, décompte
de lignes, AST, complexité, imports, dépendances déclarées et détection des
tests, le tout persisté et rendu dans les rapports comme dans les exports.

La **V0.3 — Project Intelligence** est livrée (étapes 19 à 23) : catégorie
`security`, score dérivé des findings (Docs/Tests/CI/Security/Overall),
`githor compare`, et son historique dans `githor report`.

La **V0.4 — AI Advisor** est livrée (étapes 24 à 28) : client Ollama local,
priorisation déterministe des constats ouverts, `githor advise`, et
persistance des recommandations produites. Premier livrable : le conseiller
par dépôt (§33 du cahier des charges).

La **0.5.0** livre le second volet, jusque-là laissé de côté : le conseiller
multi-dépôts en langage naturel (§35, étapes 29 à 32), `githor ask`.

La **0.6.0** ajoute la première interface graphique du projet : `githor tui`
(étapes 33 à 36), une TUI Textual en lecture seule — liste des dépôts triée
par score, détail d'un dépôt identique à `githor report`.

La **0.7.0** revient sur ce choix : la TUI jugée trop pauvre à l'usage,
`githor web` (étapes 37 à 40) la remplace comme interface recommandée — une
interface web locale, en lecture seule elle aussi, avec le même contenu mais
en HTML/CSS. La TUI reste disponible, sans être mise en avant. Le détail est
dans le [CHANGELOG](CHANGELOG.md).

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

**La V0.2 a été conçue sous cette contrainte** : elle n'ajoute que des tables
neuves — `code_audits` et les cinq qui en pendent — et ne modifie aucune colonne
existante. Les bases créées par la 0.1.0 s'ouvrent donc sans rien perdre. Ce
n'est pas une coïncidence mais une limite acceptée : elle a écarté d'emblée
toute idée d'enrichir `repository_snapshots` avec des mesures de code.

La contrainte tiendra tant qu'une évolution ne réclamera pas de colonne. La
V0.3 aurait pu être celle-là — elle voulait des scores comparables — mais elle
n'a ajouté ni table ni colonne : voir « Le score aussi est dérivé », plus haut.
La question reste ouverte pour la V0.4.

## Trajectoire

| Version | Contenu | État |
|---|---|---|
| V0.1 | Inventaire, snapshots, findings, exports, rapports | livrée (0.1.0) |
| **V0.2** | Code Auditor : clone local, AST, LOC, complexité, imports, dépendances, tests | livrée |
| **V0.3** | Project Intelligence : catégorie security, score dérivé, comparaison, historique | livrée |
| V0.4 | AI Advisor : client Ollama, priorisation déterministe, `githor advise` | livrée |
| 0.5.0 | Conseiller de projets multi-dépôts (§35), `githor ask` | livrée |
| 0.6.0 | Interface graphique : TUI Textual (`githor tui`), lecture seule | livrée, non recommandée |
| 0.7.0 | Interface web locale (`githor web`), lecture seule | livrée |
| **0.8.0** | Contrôles éditoriaux (mentions légales, à propos, lien, mise à jour auto) | livrée |

Les couches sont séparées pour cela : `github/` ne connaît ni la base ni la CLI,
les `collectors/` font le pont vers les modèles normalisés, les `rules/` ne
connaissent ni GitHub ni SQLite. La V0.2 a mis ce pari à l'épreuve : `vcs/` et
`analysis/` se sont ajoutés comme une source de plus, alimentant les mêmes
modèles, sans qu'une ligne de la V0.1 ait eu à être réécrite.

## Spécification d'origine

Le cahier des charges initial est conservé dans le dépôt
([CAHIER-DES-CHARGES.md](CAHIER-DES-CHARGES.md)). Il reste la référence de ce que
la V0.1 doit livrer, y compris ce qui n'est pas encore fait. Toute divergence
entre ce document et le code est soit un manque à combler, soit une décision à
consigner ici.
