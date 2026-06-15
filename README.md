# codetoia

Regroupe les sources d'un **dépôt git** en **un seul bloc XML compact**,
copiable/collable dans un chat IA (ChatGPT, Claude…). Objectif : minimum de tokens,
contenu intégral, prompt précis indiquant au LLM d'agir comme ingénieur logiciel.

## Utilisation

Quelques exemples (le répertoire doit être un dépôt git) :

```bash
python3 codetoia.py .                  # → fichier <repo>-dump.xml dans le dossier courant
python3 codetoia.py . -o dump.xml      # → fichier nommé explicitement
python3 codetoia.py . --stdout         # → sortie standard
python3 codetoia.py . -c               # → fichier + copie presse-papier
python3 codetoia.py . --compress       # gain max : retire commentaires + lignes vides
python3 codetoia.py . --signatures     # ⚙ signatures seules (Go/CS/C/C++/JS/TS/RF)
python3 codetoia.py . --callgraph      # ⚙ ajoute le graphe d'appel (Go, C#, C, C++, JS, TS, Robot)
python3 codetoia.py . --architecture   # ⚙ = --signatures + --callgraph
python3 codetoia.py --setup            # installe les libs des options ⚙ (1 fois/machine)
python3 codetoia.py . --include py,ts   # uniquement certaines extensions
python3 codetoia.py . --lang go,cs      # uniquement un/des langage(s) : Go/CS/C/C++/JS/TS/RF
python3 codetoia.py . --no-prompt       # sans le prompt d'instruction (actif par défaut)
python3 codetoia.py . --diff            # message de suivi : SEULEMENT les modifs non commitées
python3 codetoia.py . --diff main-feature   # message de suivi : diff entre deux réfs (sha/tag/branche)
python3 codetoia.py . --split           # découpe en N prompts ≤ 50000 c. (parts nommées par chemin)
python3 codetoia.py . --split 80000     # même chose, limite par prompt à 80000 caractères
python3 codetoia.py . --exclude "tests/*,output.txt"
```

**Référence complète des options** — bloc ci-dessous **généré depuis `--help`** (la
source de vérité). Régénère-le après toute modif de la CLI : `python3 codetoia.py --readme`.

<!-- BEGIN: codetoia --help (généré par `codetoia --readme`, ne pas éditer) -->
```text
usage: codetoia [-h] [-o OUTPUT] [--stdout] [-c] [--include EXT | --lang LANG]
                [--exclude GLOB] [--diff [A-B]] [--strip-comments]
                [--strip-blank] [--compress] [--signatures] [--callgraph]
                [--architecture] [--setup] [--no-mask-secrets]
                [--no-dedup-comments] [--no-prompt] [--split [CHARS]]
                [--no-tree] [--max-size KB] [--keep-empty]
                [path]

Regroupe les sources d'un dépôt git en un bloc XML pour une IA.

positional arguments:
  path                 Racine du dépôt (défaut: .)

options:
  -h, --help           show this help message and exit
  -o, --output OUTPUT  Fichier de sortie (défaut: <repo>-<options>-dump.xml
                       dans le répertoire courant)
  --stdout             Écrit le dump sur stdout
  -c, --clipboard      Copie aussi vers le presse-papier (en plus du fichier)
  --include EXT        Extensions à inclure (ex: py,ts,md)
  --lang LANG          N'inclure que les fichiers d'un/des langage(s).
                       Abréviations : Go, CS, C, C++, JS, TS, RF (ex: go,cs).
                       Exclusif avec --include.
  --exclude GLOB       Motifs glob à exclure, séparés par des virgules
  --diff [A-B]         Message de suivi ne contenant QUE le diff (à coller
                       après le dump complet, dans la même conversation). Sans
                       arg : modifs non commitées. A-B : entre deux réfs
                       sha/tag/branche (ex: main-feature, v1.0-v2.0) ; A..B
                       accepté aussi pour lever toute ambiguïté.
  --strip-comments     Retire les commentaires
  --strip-blank        Retire toutes les lignes vides
  --compress           Raccourci: --strip-comments --strip-blank
  --signatures         Go, CS, C, C++, JS, TS, RF: ne garder que les
                       signatures (corps → { ... } / étapes → ...). Repli sur
                       le contenu intégral si tree-sitter absent.
  --callgraph          Ajoute une section <call_graph> appelant→appelés +
                       index inversé (Go, C#, C, C++, JS, TS, Robot).
  --architecture       Raccourci: --signatures + --callgraph.
  --setup              Installe (une fois, internet requis) tree-sitter &
                       tiktoken dans un .venv local pour activer
                       --signatures/--callgraph. Ensuite le script s'en sert
                       automatiquement.
  --no-mask-secrets    Désactive le masquage des secrets (actif par défaut :
                       clés privées, tokens, secret="..." → [redacted]).
  --no-dedup-comments  Désactive la factorisation des blocs de commentaires
                       répétés (active par défaut, avec garde-fou anti-
                       augmentation).
  --no-prompt          N'inclut pas le <prompt> d'instruction en tête (actif
                       par défaut : cadre le LLM en ingénieur, légende des
                       conventions).
  --split [CHARS]      Découpe le dépôt en plusieurs prompts de longueur
                       limitée (défaut: 50000 caractères), regroupés en
                       suivant l'arborescence. Produit un message
                       d'introduction (structure + manifeste) puis N parties
                       nommées d'après leur chemin : <base>-part-00-index-
                       dump.xml, -part-01-<chemin>-dump.xml, … Un fichier plus
                       gros que la limite est isolé (non coupé).
  --no-tree            N'inclut pas l'arborescence
  --max-size KB        Ignore les fichiers > KB (défaut: 512, 0 = illimité)
  --keep-empty         Garde les fichiers vides
```
<!-- END: codetoia --help -->

> **⚙ = nécessite `--setup`** (libs natives `tree-sitter`) : `--signatures`,
> `--callgraph`, `--architecture`. Sans, ces options s'exécutent quand même mais
> **se replient sur le contenu intégral** (avec un avertissement). Le **comptage exact
> des tokens** (`tiktoken`) en dépend aussi — sinon estimation `chars/4`. Tout le reste
> tourne en **Python standard, sans `--setup`**.

**Sortie par défaut** : un fichier écrit dans le répertoire courant, nommé
`<repo>-<options>-dump.xml` où les options actives sont rappelées (ex.
`deadthisday-architecture-dump.xml`, `monrepo-signatures-compress-go-dump.xml`).
`-o` impose un nom, `--stdout` écrit sur la sortie standard, `-c` ajoute une copie
dans le presse-papier.

## Économie de tokens — comment

1. **Filtrage** : la sélection passe par
   `git ls-files --exclude-standard` (fichiers suivis + non-suivis non-ignorés),
   donc le `.gitignore`, `.git/info/exclude` et le gitignore global sont respectés
   exactement. En plus : lock files, binaires et images exclus d'office.
2. **Détection et factorisation commentaires redondants** (entêtes copyright, standard..)
3. **`--signatures`** (Go, CS, C, C++, JavaScript, TypeScript, Robot Framework) :
   ne garde que les signatures. Corps de fonctions remplacés par `{ ... }` (étapes
   Robot par `...`) ; types/structs/interfaces/imports/commentaires — et
   `[Arguments]`/`[Documentation]` en Robot — conservés. Vue « architecture » d'un
   gros projet à coût minimal (~40 % de tokens en moins mesuré sur un projet Go).
   **`--architecture`** = `--signatures` + `--callgraph` en une fois.
4. **`--compress`** : suppression des commentaires (best-effort) et lignes vides.
5. **Toujours** : espaces de fin coupés, lignes vides multiples réduites à une.

L'indentation (tabulations / espaces de début de ligne) est **conservée
volontairement** : la compresser casserait Python, YAML et les Makefile, et les
tokenizers récents (o200k) l'encodent déjà efficacement.

## Format de sortie : XML

Balises explicites, recommandées par Anthropic/Google/OpenAI pour maximiser
l'attention du modèle sur de gros contextes :

```xml
<file_summary>Projet … — N fichiers …</file_summary>
<directory_structure>…</directory_structure>
<files>
<file path="src/main.py">
<code intégral>
</file>
</files>
```

Le contenu des fichiers n'est **pas** échappé (`<`, `>`, `&` laissés tels quels) :
l'échappement gonflerait les tokens sur le code riche en chevrons (JSX, génériques).
Ce n'est donc pas du XML strictement valide, mais les délimiteurs restent clairs.

Une estimation de tokens (tiktoken si installé, sinon chars/4) est affichée à
chaque exécution.

## Prompt spécialisé intégré automatiquement

```xml
<prompt>
<role>Tu es un ingénieur logiciel senior.</role>
<context>Le dépôt de code complet est fourni ci-dessous. Des questions d'ingénieur précises sur ce code vont suivre.</context>
<format_notes>Le code est dans la section « files » : une entrée par fichier, identifiée par son attribut path ; « directory_structure » donne l'arborescence. Un corps remplacé par « { ... } » (étapes Robot par « ... ») signifie que seule la signature est conservée (implémentation masquée). « call_graph » liste les appels internes au projet : « appelant -> appelés », puis l'index inverse « appelés <- appelants » (analyse d'impact). Les marqueurs « [common-N] » dans les fichiers renvoient à la section « common_comments » (blocs de commentaires partagés, factorisés). « [redacted] » remplace un secret masqué.</format_notes>
<instructions>Analyse le code pour pouvoir y répondre avec exactitude. Cite les fichiers par leur chemin ; si une information n'y figure pas, dis-le, n'invente rien. Attends les questions.</instructions>
</prompt>
```

## Détails de certaines options

**`--diff`** produit un **message de suivi autonome** : la sortie ne contient **que**
le `<git_diff>` (pas le code du dépôt), précédée d'un `<prompt>` recadré. Le flux visé :

1. tu colles d'abord le **dump complet** du dépôt (sans `--diff`) dans le chat ;
2. plus tard, dans **la même conversation**, tu colles un `--diff` pour soumettre des
   changements — le modèle les rapporte au code déjà fourni, sans renvoyer tout le dépôt.

Le diff peut être :
- **sans argument** → modifs non commitées (`git diff HEAD`) ;
- **`A-B`** → diff entre deux réfs, où `A` et `B` sont des **sha, tags ou branches**
  mélangeables (ex. `main-feature`, `v1.0-v2.0`, `abc123-main`). Une branche désigne
  son dernier commit.

Le séparateur `-` est **désambiguïsé via git** : comme une branche/tag peut contenir
des `-` (`feature/my-thing`), chaque découpe candidate est validée par
`git rev-parse` ; si plusieurs sont valides, codetoia le signale et tu utilises la
forme `A..B` (acceptée aussi). Le masquage de secrets s'applique au diff. Pas besoin
de `--setup` (git pur). En mode `--diff`, le `<prompt>` est recadré en **message de
suivi** : « le dépôt t'a déjà été fourni ; voici uniquement les changements » et oriente
vers la revue (correction, effets de bord, impact, en s'appuyant sur le call_graph déjà transmis).

**`--split [CHARS]`** découpe un gros dépôt en **plusieurs prompts** de longueur limitée
(défaut **50000 caractères**), à coller **dans l'ordre, dans la même conversation** :

1. un **message d'introduction** (`…-part-00-index-dump.xml`) : `<prompt>` global cadrant le
   LLM, **arborescence complète**, conventions, `common_comments`/`call_graph` partagés, et un
   **manifeste** « partie k → tels répertoires » ; il demande au modèle d'**attendre toutes
   les parties** avant de répondre ;
2. puis **N parties** nommées d'après leur **chemin** (`…-part-01-<chemin>-dump.xml`, p. ex.
   `-part-02-src-dump.xml`), chacune avec un court en-tête « partie k/N » et ses fichiers ; la
   dernière annonce que le modèle peut répondre. Le numéro préserve l'ordre de collage et
   distingue deux parties d'un même répertoire scindé. Le suffixe `-dump.xml` les fait
   ignorer par le `.gitignore` par défaut.

Le découpage **suit l'arborescence** : on évalue la taille (caractères) de chaque
sous-répertoire ; un sous-répertoire qui tient en entier reste **groupé** (et peut
fusionner avec ses frères) ; un sous-répertoire trop gros est **scindé en ses propres
parties**. La limite porte sur les fichiers d'une partie (le petit en-tête s'ajoute). Un
**fichier seul plus gros que la limite n'est pas coupé** (on ne tronçonne pas du code au
milieu d'une fonction) : il est **isolé** dans sa partie, qui dépasse alors la limite — c'est
signalé sur stderr. Pour les gros fichiers, `--signatures`/`--architecture` réduit les corps
et les fait rentrer. Sortie : des fichiers `…-part-*.xml` (ou, avec `--stdout`, tout
concaténé avec des séparateurs `===== part k =====`). Compatible avec
`--signatures`/`--compress`/`--lang`… ; `--diff`, lui, court-circuite le découpage. La
**taille de chaque partie** (caractères + tokens) est affichée automatiquement sur stderr.

Un bloc **`<prompt>`** d'instruction est **préfixé par défaut** : il cadre le modèle en
**ingénieur logiciel**, lui annonce que des questions précises sur le code vont suivre,
et lui donne la **légende des conventions** (adaptée aux options : `{ ... }` = signature,
`[common-N]`, `call_graph`, `[redacted]`) plus les contraintes (citer les fichiers, ne
rien inventer, attendre les questions). `--no-prompt` pour le retirer.

Le **masquage de secrets** (équivalent léger du SecretLint) est **actif
par défaut** : il remplace par `[redacted]` les clés privées (`-----BEGIN ... PRIVATE
KEY-----`), tokens GitHub/AWS/Slack/Google/Stripe, JWT, et affectations
`password|secret|api_key="..."`. On **masque** au lieu d'exclure le fichier — le code
reste lisible. Un récap s'affiche sur stderr quand quelque chose est masqué ;
`--no-mask-secrets` désactive complètement la passe. C'est une heuristique haute
confiance, pas exhaustive : un complément de prudence, pas un substitut à une vraie
gestion de secrets.

La **factorisation des commentaires répétés** est **active par défaut** : les **blocs
répétés** (typiquement les en-têtes de licence/copyright en tête de chaque fichier —
100 fichiers Apache‑2.0 = 100 fois le même bloc) sont émis **une seule fois** dans
`<common_comments>` et chaque fichier les référence par `[common-N]`.

La détection est **fine** (ligne à ligne, via *ancre + extension par unanimité*) :
elle factorise le plus grand fragment commun même quand l'en-tête **varie
partiellement** d'un fichier à l'autre (ex. le pavé GPL identique est extrait alors que
la ligne de description ou de copyright, propre à chaque fichier, reste en place). Les
délimiteurs `/* */` sont préservés. C'est **agnostique au langage** (marqueurs du
registre) et matche même un en-tête identique écrit en `//` et en `#`. Un **garde-fou
« gain net »** ne factorise que si ça économise réellement — jamais d'augmentation.
Sans objet (ignoré) avec `--compress`. `--no-dedup-comments` pour désactiver.

Le **callgraph** est une heuristique *syntaxique* (tree-sitter) limitée aux appels
**intra-projet** : les appels stdlib/bibliothèques sont écartés. Les appelants sont
qualifiés par dossier (Go), classe (C#, C++, JS/TS) ou fichier (C, fonctions libres,
Robot) ; `.ts` et `.tsx` partagent un même graphe. Les appelés sont appariés par nom,
donc un nom de méthode présent dans plusieurs types/classes peut produire une arête
approximative. Pour un graphe exact, il faudrait une analyse de types par langage
(ex. `go/callgraph`), volontairement non retenue ici.

## Modes signatures / callgraph : installation

`--signatures`, `--callgraph` (donc aussi `--architecture`) et le comptage exact des
tokens reposent sur des libs natives (`tree-sitter`, `tiktoken`). Le cœur du script
n'en dépend pas — sans elles, il tourne quand même (repli sur le contenu intégral +
estimation chars/4).

Pour les activer, **une seule commande, une fois par machine** (internet requis) :

```bash
python3 codetoia.py --setup
```

Elle crée un `.venv` à côté du script et y installe les libs. Ensuite, tu continues
à lancer la commande habituelle :

```bash
python3 codetoia.py . --architecture   # ou --signatures / --callgraph
```

Le script détecte son `.venv` et **se relance dedans automatiquement** — tu n'as
jamais à activer ni gérer le venv toi-même. Sur une machine sans `--setup` (ou hors
ligne), ces modes affichent un avertissement et conservent le contenu intégral.

Prérequis : un Python disposant des modules standard `venv` et `ensurepip` (présents
dans toute install standard ; sur Debian/Ubuntu minimal : `apt install python3-venv`).

## Architecture du code

```
codetoia.py            # cœur : CLI, collecte git, transform, sortie XML, setup/venv
codetoia_langs/        # tout ce qui est spécifique à un langage (un module par langage)
    base.py            # descripteur Lang, helpers tree-sitter, chargement parsers
    go.py csharp.py c.py cpp.py javascript.py typescript.py robot.py
    __init__.py        # registre : agrège les Lang et expose l'API au cœur
```

Pour **ajouter un langage**, créer `codetoia_langs/xx.py` qui définit un `Lang`
(extensions, grammaire tree-sitter, `body_containers` pour les signatures,
éventuellement `extract_calls` pour le callgraph, marqueurs de commentaires), puis
l'ajouter à la liste `LANGS` du `__init__`. Le cœur ne change pas.

À déployer : copier `codetoia.py` **et** le dossier `codetoia_langs/` (le `.venv`
créé par `--setup` reste, lui, propre à chaque machine).

## Licence

[MIT](LICENSE) — librement utilisable, modifiable et redistribuable, à condition de
conserver l'avis de copyright et le texte de licence (c'est l'attribution).

Auteur : **jcb** — développé avec l'assistance de **Claude** (Anthropic).
