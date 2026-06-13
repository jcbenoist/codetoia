# codetoia

Regroupe les sources d'un **dépôt git** en **un seul bloc XML compact**,
copiable/collable dans un chat IA (ChatGPT, Claude…). Objectif : minimum de tokens,
contenu intégral.

## Utilisation

```bash
python3 codetoia.py .                  # tout le dépôt → presse-papier (+ résumé tokens)
python3 codetoia.py . -o dump.xml      # → fichier
python3 codetoia.py . --stdout         # → sortie standard
python3 codetoia.py . --compress       # gain max : retire commentaires + lignes vides
python3 codetoia.py . --include py,ts   # uniquement certaines extensions
python3 codetoia.py . --exclude "tests/*,output.txt"
```

Le répertoire doit être un dépôt git (sinon erreur).

## Économie de tokens — comment

1. **Filtrage** (principal gain) : la sélection passe par
   `git ls-files --exclude-standard` (fichiers suivis + non-suivis non-ignorés),
   donc le `.gitignore`, `.git/info/exclude` et le gitignore global sont respectés
   exactement. En plus : lock files, binaires et images exclus d'office.
2. **`--signatures`** (Go & C#) : ne garde que les signatures — corps de fonctions
   remplacés par `{ ... }`, types/structs/interfaces/imports/commentaires conservés.
   Vue « architecture » d'un gros projet à coût minimal (~40 % de tokens en moins
   mesuré sur un projet Go).
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
| `-o, --output` | écrit dans un fichier |
| `--stdout` | écrit sur la sortie standard |
| `-c, --clipboard` | force la copie presse-papier |
| `--signatures` | Go & C# : ne garder que les signatures (corps → `{ ... }`) |
| `--compress` | `--strip-comments` + `--strip-blank` |
| `--strip-comments` / `--strip-blank` | au choix |
| `--include EXT,…` | liste blanche d'extensions |
| `--exclude GLOB,…` | motifs à exclure |
| `--no-tree` | sans arborescence |
| `--max-size KB` | saute les fichiers volumineux (défaut 512) |
| `--keep-empty` | garde les fichiers vides |

## Dépendances optionnelles

Aucune dépendance obligatoire — le script tourne tel quel.

- `pip install tiktoken` → comptage de tokens exact (sinon estimation chars/4).
- `pip install tree-sitter tree-sitter-go tree-sitter-c-sharp` → active `--signatures`.
  Si absent, `--signatures` affiche un avertissement et conserve le contenu intégral
  (le script s'exécute quand même).
