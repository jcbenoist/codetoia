#!/usr/bin/env python3
"""codetoia — regroupe les sources d'un projet en un seul bloc copiable pour une IA.

Objectif : sortie la plus compacte possible (économie de tokens) tout en restant
intégrale et facilement compréhensible par un LLM (ChatGPT en priorité).

Usage minimal :
    python codetoia.py .              # arbre + tous les fichiers texte, vers le presse-papier
    python codetoia.py . -o dump.txt  # écrit dans un fichier
    python codetoia.py . --compress   # strip commentaires + lignes vides (gain max)

Voir --help pour toutes les options.
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------- #
# Filtrage : ce qui ne doit JAMAIS finir dans le dump (plus gros gain de tokens)
# --------------------------------------------------------------------------- #

# Dossiers ignorés où qu'ils soient dans l'arborescence.
DEFAULT_IGNORE_DIRS = {
    ".git", ".hg", ".svn", ".idea", ".vscode", ".vs",
    "node_modules", "bower_components", "vendor",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    ".venv", "venv", "env", ".env.d",
    "dist", "build", "out", "target", ".next", ".nuxt", ".svelte-kit",
    ".cache", ".parcel-cache", "coverage", ".nyc_output",
    ".gradle", ".dart_tool", "Pods", "DerivedData",
}

# Fichiers ignorés par nom exact.
DEFAULT_IGNORE_FILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "npm-shrinkwrap.json",
    "Cargo.lock", "poetry.lock", "Pipfile.lock", "composer.lock", "Gemfile.lock",
    "go.sum", "bun.lockb", ".DS_Store", "Thumbs.db",
}

# Extensions binaires / non pertinentes : exclues d'office.
BINARY_EXTS = {
    # images
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tiff", ".svg",
    ".heic", ".avif",
    # fonts
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    # media
    ".mp3", ".wav", ".ogg", ".flac", ".mp4", ".mov", ".avi", ".mkv", ".webm",
    # archives / binaires
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war",
    ".exe", ".dll", ".so", ".dylib", ".o", ".a", ".class", ".pyc", ".pyo",
    ".wasm", ".bin", ".dat", ".db", ".sqlite", ".sqlite3",
    # documents binaires
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    # divers
    ".lock", ".map", ".min.js", ".min.css",
}

# Commentaires par langage : (préfixe ligne, (bloc_ouvrant, bloc_fermant) | None)
# Utilisé uniquement avec --compress / --strip-comments (best-effort, conservateur).
LINE_COMMENT = {
    ".py": "#", ".rb": "#", ".sh": "#", ".bash": "#", ".zsh": "#", ".yaml": "#",
    ".yml": "#", ".toml": "#", ".pl": "#", ".r": "#", ".jl": "#",
    ".js": "//", ".ts": "//", ".jsx": "//", ".tsx": "//", ".c": "//", ".h": "//",
    ".cpp": "//", ".hpp": "//", ".cc": "//", ".java": "//", ".go": "//",
    ".rs": "//", ".swift": "//", ".kt": "//", ".cs": "//", ".php": "//",
    ".scala": "//", ".dart": "//",
}
BLOCK_COMMENT = {
    ext: ("/*", "*/")
    for ext in (".js", ".ts", ".jsx", ".tsx", ".c", ".h", ".cpp", ".hpp", ".cc",
                ".java", ".go", ".rs", ".swift", ".kt", ".cs", ".php", ".scala",
                ".css", ".scss", ".less", ".dart")
}


# --------------------------------------------------------------------------- #
# Lecture de .gitignore (sous-ensemble raisonnable du format)
# --------------------------------------------------------------------------- #

@dataclass
class GitIgnore:
    """Matcher .gitignore simplifié : motifs négatifs, ancrage, et /dir/."""
    patterns: list[tuple[str, bool, bool]]  # (motif, négatif, dossier_only)

    @classmethod
    def load(cls, root: Path) -> "GitIgnore":
        patterns: list[tuple[str, bool, bool]] = []
        gi = root / ".gitignore"
        if gi.exists():
            for raw in gi.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                negative = line.startswith("!")
                if negative:
                    line = line[1:]
                dir_only = line.endswith("/")
                line = line.rstrip("/")
                patterns.append((line, negative, dir_only))
        return cls(patterns)

    def ignored(self, rel: str, is_dir: bool) -> bool:
        result = False
        for pat, negative, dir_only in self.patterns:
            if dir_only and not is_dir:
                continue
            if self._match(pat, rel):
                result = not negative
        return result

    @staticmethod
    def _match(pat: str, rel: str) -> bool:
        name = rel.split("/")[-1]
        anchored = pat.startswith("/")
        pat = pat.lstrip("/")
        if anchored:
            return fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(rel, pat + "/*")
        # non ancré : match sur le chemin complet ou n'importe quel segment terminal
        return (
            fnmatch.fnmatch(rel, pat)
            or fnmatch.fnmatch(rel, "*/" + pat)
            or fnmatch.fnmatch(name, pat)
            or fnmatch.fnmatch(rel, pat + "/*")
            or fnmatch.fnmatch(rel, "*/" + pat + "/*")
        )


# --------------------------------------------------------------------------- #
# Collecte des fichiers
# --------------------------------------------------------------------------- #

def is_binary(path: Path, sniff: int = 8192) -> bool:
    """Détecte un binaire via octet nul dans les premiers Ko."""
    try:
        with path.open("rb") as f:
            return b"\x00" in f.read(sniff)
    except OSError:
        return True


def git_files(root: Path) -> list[Path] | None:
    """Liste via `git ls-files` (suivi + non-suivi non-ignoré). None si hors dépôt.

    Délègue tout le filtrage .gitignore à git : .gitignore (à tous niveaux),
    .git/info/exclude et le core.excludesFile global sont respectés exactement.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached", "--others",
             "--exclude-standard", "-z"],
            capture_output=True,
        )
    except (FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None  # pas un dépôt git (ou git absent)
    names = proc.stdout.decode("utf-8", "replace").split("\0")
    seen: set[str] = set()
    files: list[Path] = []
    for n in names:
        if not n or n in seen:
            continue
        seen.add(n)
        p = root / n
        if p.is_file():  # exclut d'office les gitlinks (sous-modules)
            files.append(p)
    return files


def accept(p: Path, rel: str, opts: argparse.Namespace,
           include_exts: set[str] | None) -> bool:
    """Filtres indépendants du .gitignore : type/taille/binaire/include-exclude."""
    name = p.name
    if name in DEFAULT_IGNORE_FILES:
        return False
    if any(seg in DEFAULT_IGNORE_DIRS for seg in rel.split("/")[:-1]):
        return False
    ext = "".join(p.suffixes[-2:]) if name.endswith((".min.js", ".min.css")) else p.suffix
    if p.suffix.lower() in BINARY_EXTS or ext.lower() in BINARY_EXTS:
        return False
    if include_exts and p.suffix.lower() not in include_exts:
        return False
    if any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(name, pat)
           for pat in opts.exclude):
        return False
    try:
        size = p.stat().st_size
    except OSError:
        return False
    if opts.max_size and size > opts.max_size * 1024:
        return False
    if size == 0 and not opts.keep_empty:
        return False
    if is_binary(p):
        return False
    return True


def collect(root: Path, opts: argparse.Namespace) -> tuple[list[Path], str]:
    """Renvoie (fichiers, méthode). Privilégie git ; repli sur os.walk."""
    include_exts = {e if e.startswith(".") else "." + e
                    for e in opts.include} if opts.include else None

    if opts.use_git:
        candidates = git_files(root)
        if candidates is not None:
            files = [p for p in candidates
                     if accept(p, str(p.relative_to(root)).replace(os.sep, "/"),
                               opts, include_exts)]
            return sorted(files), "git"

    return collect_walk(root, opts, include_exts), "walk"


def collect_walk(root: Path, opts: argparse.Namespace,
                 include_exts: set[str] | None) -> list[Path]:
    """Repli hors dépôt git : parcours manuel + parseur .gitignore maison."""
    gitignore = GitIgnore.load(root) if opts.gitignore else GitIgnore([])
    files: list[Path] = []

    for dirpath, dirnames, filenames in os.walk(root):
        d = Path(dirpath)
        kept = []
        for name in dirnames:
            rel = str((d / name).relative_to(root)).replace(os.sep, "/")
            if name in DEFAULT_IGNORE_DIRS:
                continue
            if opts.gitignore and gitignore.ignored(rel, is_dir=True):
                continue
            kept.append(name)
        dirnames[:] = sorted(kept)

        for name in sorted(filenames):
            p = d / name
            rel = str(p.relative_to(root)).replace(os.sep, "/")
            if opts.gitignore and gitignore.ignored(rel, is_dir=False):
                continue
            if accept(p, rel, opts, include_exts):
                files.append(p)
    return files


# --------------------------------------------------------------------------- #
# Transformation du contenu (compactage)
# --------------------------------------------------------------------------- #

def strip_comments(text: str, ext: str) -> str:
    """Retire commentaires ligne + bloc (best-effort, ne parse pas les strings).

    Conservateur : ne touche pas une ligne contenant des guillemets pour éviter
    de casser un '#' ou '//' à l'intérieur d'une chaîne.
    """
    line_tok = LINE_COMMENT.get(ext)
    block = BLOCK_COMMENT.get(ext)

    if block:
        opener, closer = re.escape(block[0]), re.escape(block[1])
        text = re.sub(opener + r".*?" + closer, "", text, flags=re.DOTALL)

    if line_tok:
        out = []
        for line in text.splitlines():
            stripped = line.lstrip()
            if stripped.startswith(line_tok):
                continue  # ligne entièrement commentaire
            if line_tok in line and '"' not in line and "'" not in line and "`" not in line:
                line = line[: line.index(line_tok)].rstrip()
            out.append(line)
        text = "\n".join(out)
    return text


def transform(text: str, ext: str, opts: argparse.Namespace) -> str:
    if opts.strip_comments:
        text = strip_comments(text, ext)
    # trim espaces de fin : gain gratuit, zéro perte d'information
    lines = [ln.rstrip() for ln in text.splitlines()]
    if opts.strip_blank:
        lines = [ln for ln in lines if ln.strip()]
    else:
        # collapse 3+ lignes vides consécutives en une seule
        collapsed: list[str] = []
        blanks = 0
        for ln in lines:
            if ln:
                blanks = 0
            else:
                blanks += 1
                if blanks > 1:
                    continue
            collapsed.append(ln)
        lines = collapsed
    return "\n".join(lines).strip("\n")


# --------------------------------------------------------------------------- #
# Arbre de fichiers
# --------------------------------------------------------------------------- #

def render_tree(root: Path, files: list[Path]) -> str:
    tree: dict = {}
    for p in files:
        parts = p.relative_to(root).parts
        node = tree
        for part in parts:
            node = node.setdefault(part, {})
    lines: list[str] = []

    def walk(node: dict, prefix: str = ""):
        items = sorted(node.items(), key=lambda kv: (not kv[1], kv[0]))
        for i, (name, child) in enumerate(items):
            last = i == len(items) - 1
            lines.append(prefix + ("└ " if last else "├ ") + name)
            if child:
                walk(child, prefix + ("  " if last else "│ "))

    walk(tree)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Estimation de tokens
# --------------------------------------------------------------------------- #

def estimate_tokens(text: str) -> tuple[int, str]:
    try:
        import tiktoken  # type: ignore
        enc = tiktoken.get_encoding("o200k_base")  # GPT-4o / GPT-4.1
        return len(enc.encode(text)), "tiktoken o200k"
    except Exception:
        return round(len(text) / 4), "approx (chars/4)"


# --------------------------------------------------------------------------- #
# Sortie
# --------------------------------------------------------------------------- #

def to_clipboard(text: str) -> bool:
    """Copie vers le presse-papier (WSL clip.exe, xclip, wl-copy, pbcopy)."""
    candidates = [
        ["clip.exe"],
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["pbcopy"],
    ]
    for cmd in candidates:
        try:
            proc = subprocess.run(cmd, input=text.encode("utf-8"),
                                  capture_output=True)
            if proc.returncode == 0:
                return True
        except FileNotFoundError:
            continue
    return False


def build_output(root: Path, files: list[Path], opts: argparse.Namespace) -> str:
    chunks: list[str] = []
    project = root.resolve().name
    chunks.append(f"# {project} — {len(files)} fichiers")
    if not opts.no_tree:
        chunks.append("# Arborescence:\n" + render_tree(root, files))
    chunks.append("# Chaque fichier suit le marqueur ▼ <chemin>. Code intégral ci-dessous.\n")

    for p in files:
        rel = str(p.relative_to(root)).replace(os.sep, "/")
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            chunks.append(f"▼ {rel}\n# <illisible: {e}>")
            continue
        content = transform(raw, p.suffix.lower(), opts)
        chunks.append(f"▼ {rel}\n{content}")

    return "\n\n".join(chunks) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="codetoia",
        description="Regroupe les sources d'un projet en un bloc compact pour une IA.",
    )
    ap.add_argument("path", nargs="?", default=".", help="Répertoire racine (défaut: .)")
    ap.add_argument("-o", "--output", help="Fichier de sortie (sinon: presse-papier + résumé)")
    ap.add_argument("--stdout", action="store_true", help="Écrit le dump sur stdout")
    ap.add_argument("-c", "--clipboard", action="store_true",
                    help="Force la copie vers le presse-papier")
    ap.add_argument("--include", metavar="EXT", help="Extensions à inclure (ex: py,ts,md)")
    ap.add_argument("--exclude", metavar="GLOB", default="",
                    help="Motifs glob à exclure, séparés par des virgules")
    ap.add_argument("--strip-comments", action="store_true", help="Retire les commentaires")
    ap.add_argument("--strip-blank", action="store_true", help="Retire toutes les lignes vides")
    ap.add_argument("--compress", action="store_true",
                    help="Raccourci: --strip-comments --strip-blank")
    ap.add_argument("--no-tree", action="store_true", help="N'inclut pas l'arborescence")
    ap.add_argument("--no-git", dest="use_git", action="store_false",
                    help="N'utilise pas `git ls-files` (force le parcours manuel)")
    ap.add_argument("--no-gitignore", dest="gitignore", action="store_false",
                    help="En mode parcours manuel, ignore le .gitignore")
    ap.add_argument("--max-size", type=int, default=512, metavar="KB",
                    help="Ignore les fichiers > KB (défaut: 512, 0 = illimité)")
    ap.add_argument("--keep-empty", action="store_true", help="Garde les fichiers vides")
    args = ap.parse_args(argv)

    if args.compress:
        args.strip_comments = True
        args.strip_blank = True
    args.include = [e.strip() for e in args.include.split(",")] if args.include else None
    args.exclude = [e.strip() for e in args.exclude.split(",") if e.strip()]

    root = Path(args.path)
    if not root.is_dir():
        print(f"Erreur: '{root}' n'est pas un répertoire.", file=sys.stderr)
        return 2

    files, src = collect(root, args)
    if not files:
        print("Aucun fichier source trouvé.", file=sys.stderr)
        return 1

    output = build_output(root, files, args)
    tokens, method = estimate_tokens(output)

    if args.stdout:
        sys.stdout.write(output)
        return 0

    src_label = "git" if src == "git" else "parcours"
    summary = (
        f"✓ {len(files)} fichiers ({src_label}) — {len(output):,} caractères — "
        f"~{tokens:,} tokens ({method})"
    ).replace(",", " ")

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"{summary}\n→ écrit dans {args.output}", file=sys.stderr)
        if args.clipboard and to_clipboard(output):
            print("→ copié dans le presse-papier", file=sys.stderr)
    else:
        if to_clipboard(output):
            print(f"{summary}\n→ copié dans le presse-papier", file=sys.stderr)
        else:
            print(f"{summary}\n(presse-papier indisponible — utilise -o ou --stdout)",
                  file=sys.stderr)
            sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
