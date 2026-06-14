# codetoia

Regroupe les sources d'un **dépôt git** en **un seul bloc XML compact**,
copiable/collable dans un chat IA (ChatGPT, Claude…). Objectif : minimum de tokens,
contenu intégral.

## Utilisation

```bash
python3 codetoia.py .                  # → fichier <repo>-dump.xml dans le dossier courant
python3 codetoia.py . -o dump.xml      # → fichier nommé explicitement
python3 codetoia.py . --stdout         # → sortie standard
python3 codetoia.py . -c               # → fichier + copie presse-papier
python3 codetoia.py . --compress       # gain max : retire commentaires + lignes vides
python3 codetoia.py . --signatures     # signatures seules (Go/CS/C/C++/JS/TS/RF)
python3 codetoia.py . --callgraph      # ajoute le graphe d'appel (Go, C#, C, C++, JS, TS, Robot)
python3 codetoia.py . --architecture   # = --signatures + --callgraph
python3 codetoia.py . --include py,ts   # uniquement certaines extensions
python3 codetoia.py . --lang go,cs      # uniquement un/des langage(s) : Go/CS/C/C++/JS/TS/RF
python3 codetoia.py . --exclude "tests/*,output.txt"
```

Le répertoire doit être un dépôt git (sinon erreur).

**Sortie par défaut** : un fichier écrit dans le répertoire courant, nommé
`<repo>-<options>-dump.xml` où les options actives sont rappelées (ex.
`deadthisday-architecture-dump.xml`, `monrepo-signatures-compress-go-dump.xml`).
`-o` impose un nom, `--stdout` écrit sur la sortie standard, `-c` ajoute une copie
dans le presse-papier.

## Économie de tokens — comment

1. **Filtrage** (principal gain) : la sélection passe par
   `git ls-files --exclude-standard` (fichiers suivis + non-suivis non-ignorés),
   donc le `.gitignore`, `.git/info/exclude` et le gitignore global sont respectés
   exactement. En plus : lock files, binaires et images exclus d'office.
2. **`--signatures`** (Go, CS, C, C++, JavaScript, TypeScript, Robot Framework) :
   ne garde que les signatures. Corps de fonctions remplacés par `{ ... }` (étapes
   Robot par `...`) ; types/structs/interfaces/imports/commentaires — et
   `[Arguments]`/`[Documentation]` en Robot — conservés. Vue « architecture » d'un
   gros projet à coût minimal (~40 % de tokens en moins mesuré sur un projet Go).
   **`--architecture`** = `--signatures` + `--callgraph` en une fois.
3. **`--compress`** : suppression des commentaires (best-effort) et lignes vides.
4. **Toujours** : espaces de fin coupés, lignes vides multiples réduites à une.

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

## Options

| Option | Effet |
|--------|-------|
| `-o, --output` | nom de fichier explicite (sinon `<repo>-<options>-dump.xml`) |
| `--stdout` | écrit sur la sortie standard |
| `-c, --clipboard` | copie *aussi* dans le presse-papier (en plus du fichier) |
| `--signatures` | Go/CS/C/C++/JS/TS/RF : ne garder que les signatures (corps → `{ ... }`) |
| `--callgraph` | ajoute `<call_graph>` (Go, C#, C, C++, JS, TS, Robot) : appelant→appelés + index inversé appelés←appelants (analyse d'impact) ; une section par langage |
| `--architecture` | raccourci : `--signatures` + `--callgraph` |
| `--compress` | `--strip-comments` + `--strip-blank` |
| `--strip-comments` / `--strip-blank` | au choix |
| `--include EXT,…` | liste blanche d'extensions |
| `--lang LANG,…` | n'inclure qu'un/des langage(s) — `Go CS C C++ JS TS RF` ; exclusif de `--include` |
| `--exclude GLOB,…` | motifs à exclure |
| `--no-mask-secrets` | désactive le masquage des secrets (**actif par défaut**) |
| `--no-dedup-comments` | désactive la factorisation des commentaires répétés (**active par défaut**) |
| `--no-tree` | sans arborescence |
| `--max-size KB` | saute les fichiers volumineux (défaut 512) |
| `--keep-empty` | garde les fichiers vides |

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
