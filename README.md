# codetoia

Regroupe toutes les sources d'un projet (récursif) en **un seul bloc compact**,
copiable/collable dans un chat IA (ChatGPT, Claude…). Objectif : minimum de tokens,
contenu intégral.

## Utilisation

```bash
python3 codetoia.py .                 # tout le projet → presse-papier (+ résumé tokens)
python3 codetoia.py . -o dump.txt     # → fichier
python3 codetoia.py . --stdout        # → sortie standard
python3 codetoia.py . --compress      # gain max : retire commentaires + lignes vides
python3 codetoia.py . --include py,ts  # uniquement certaines extensions
python3 codetoia.py . --exclude "tests/*,*.spec.ts"
```

## Économie de tokens — comment

1. **Filtrage** (principal gain) : `.git`, `node_modules`, `dist`, lock files,
   binaires, images… exclus d'office. Dans un dépôt git, la sélection passe par
   `git ls-files` (fichiers suivis + non-suivis non-ignorés) — le `.gitignore`,
   `.git/info/exclude` et le gitignore global sont donc respectés exactement.
   Hors dépôt git (ou avec `--no-git`), repli sur un parcours manuel + parseur
   `.gitignore` interne.
2. **`--compress`** : suppression des commentaires (best-effort) et lignes vides.
3. **Toujours** : espaces de fin coupés, lignes vides multiples réduites.

## Format de sortie

```
# <projet> — N fichiers
# Arborescence:
└ src
  └ main.py
▼ src/main.py
<code intégral>
```

Le marqueur `▼ <chemin>` délimite chaque fichier — distinctif, ~1 token, facile
à repérer pour un LLM. Une estimation de tokens (tiktoken si installé, sinon
chars/4) est affichée à chaque exécution.

## Options

| Option | Effet |
|--------|-------|
| `-o, --output` | écrit dans un fichier |
| `--stdout` | écrit sur la sortie standard |
| `-c, --clipboard` | force la copie presse-papier |
| `--compress` | `--strip-comments` + `--strip-blank` |
| `--strip-comments` / `--strip-blank` | au choix |
| `--include EXT,…` | liste blanche d'extensions |
| `--exclude GLOB,…` | motifs à exclure |
| `--no-tree` | sans arborescence |
| `--no-git` | n'utilise pas `git ls-files` (force le parcours manuel) |
| `--no-gitignore` | en mode parcours, ignore le `.gitignore` |
| `--max-size KB` | saute les fichiers volumineux (défaut 512) |

Aucune dépendance obligatoire. `pip install tiktoken` pour un comptage de tokens exact.
