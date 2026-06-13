#!/usr/bin/env python3
"""codetoia — regroupe les sources d'un dépôt git en un bloc XML copiable pour une IA.

Objectif : sortie la plus compacte possible (économie de tokens) tout en restant
intégrale et bien structurée pour un LLM (ChatGPT en priorité). La sélection des
fichiers est déléguée à `git ls-files`, donc le .gitignore est respecté exactement.

Tout ce qui est spécifique à un langage (grammaire, signatures, graphe d'appel,
commentaires) vit dans le package `codetoia_langs/` (un module par langage).

Usage :
    python codetoia.py .              # tout le dépôt → presse-papier (+ résumé tokens)
    python codetoia.py . -o dump.xml  # → fichier
    python codetoia.py . --compress   # retire commentaires + lignes vides (gain max)
    python codetoia.py . --signatures   # Go/CS/C/C++/JS/TS/RF : signatures seules
    python codetoia.py . --architecture # = --signatures + --callgraph
    python codetoia.py --setup        # installe (1 fois) les libs tree-sitter/tiktoken
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
from pathlib import Path

import codetoia_langs as langs

# Fichiers de bruit (lock files, métadonnées) : exclus même si suivis par git.
DEFAULT_IGNORE_FILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "npm-shrinkwrap.json",
    "Cargo.lock", "poetry.lock", "Pipfile.lock", "composer.lock", "Gemfile.lock",
    "go.sum", "bun.lockb", ".DS_Store", "Thumbs.db",
}

# Suffixes binaires / non pertinents (le test couvre aussi .min.js, .tar.gz…).
BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tiff", ".svg",
    ".heic", ".avif", ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp3", ".wav", ".ogg", ".flac", ".mp4", ".mov", ".avi", ".mkv", ".webm",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war",
    ".exe", ".dll", ".so", ".dylib", ".o", ".a", ".class", ".pyc", ".pyo",
    ".wasm", ".bin", ".dat", ".db", ".sqlite", ".sqlite3",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".lock", ".map", ".min.js", ".min.css",
}


# --------------------------------------------------------------------------- #
# Collecte des fichiers (via git)
# --------------------------------------------------------------------------- #

def is_binary(path: Path) -> bool:
    """Détecte un binaire via un octet nul dans les premiers Ko."""
    try:
        with path.open("rb") as f:
            return b"\x00" in f.read(8192)
    except OSError:
        return True


def collect(root: Path, opts: argparse.Namespace) -> list[Path] | None:
    """Fichiers suivis + non-suivis non-ignorés, filtrés. None si hors dépôt git.

    `git ls-files --exclude-standard` applique .gitignore (tous niveaux),
    .git/info/exclude et le gitignore global exactement comme git.
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
        return None  # pas un dépôt git

    include_exts = {e if e.startswith(".") else "." + e
                    for e in opts.include} if opts.include else None
    files: list[Path] = []
    for name in proc.stdout.decode("utf-8", "replace").split("\0"):
        if not name:
            continue
        p = root / name
        if p.is_file() and accept(p, name, opts, include_exts):
            files.append(p)
    return sorted(files)


def accept(p: Path, rel: str, opts: argparse.Namespace,
           include_exts: set[str] | None) -> bool:
    """Filtres de bruit : lock files, binaires, taille, include/exclude."""
    name = p.name.lower()
    if p.name in DEFAULT_IGNORE_FILES:
        return False
    if any(name.endswith(e) for e in BINARY_EXTS):
        return False
    if include_exts and p.suffix.lower() not in include_exts:
        return False
    if any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(p.name, pat)
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
    return not is_binary(p)


# --------------------------------------------------------------------------- #
# Transformation du contenu (compactage)
# --------------------------------------------------------------------------- #

def strip_comments(text: str, ext: str) -> str:
    """Retire commentaires ligne + bloc (best-effort, ne parse pas les chaînes).

    Conservateur : ignore une ligne contenant un guillemet pour ne pas couper
    un marqueur situé à l'intérieur d'une chaîne. Les marqueurs viennent du registre.
    """
    line, block = langs.comment_markers(ext)
    if block:
        opener, closer = re.escape(block[0]), re.escape(block[1])
        text = re.sub(opener + r".*?" + closer, "", text, flags=re.DOTALL)
    if line:
        out = []
        for ln in text.splitlines():
            if ln.lstrip().startswith(line):
                continue
            if line in ln and not any(q in ln for q in "\"'`"):
                ln = ln[: ln.index(line)].rstrip()
            out.append(ln)
        text = "\n".join(out)
    return text


def transform(text: str, ext: str, opts: argparse.Namespace) -> str:
    if opts.strip_comments:
        text = strip_comments(text, ext)
    lines = [ln.rstrip() for ln in text.splitlines()]  # trim de fin : sans perte
    if opts.strip_blank:
        lines = [ln for ln in lines if ln.strip()]
    else:
        # réduit 2+ lignes vides consécutives à une seule
        out, blank = [], False
        for ln in lines:
            if ln:
                out.append(ln)
                blank = False
            elif not blank:
                out.append(ln)
                blank = True
        lines = out
    return "\n".join(lines).strip("\n")


# --------------------------------------------------------------------------- #
# Sortie XML
# --------------------------------------------------------------------------- #

def render_tree(root: Path, files: list[Path]) -> str:
    tree: dict = {}
    for p in files:
        node = tree
        for part in p.relative_to(root).parts:
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


def render_file(p: Path, opts: argparse.Namespace) -> str:
    try:
        source = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"<illisible: {e}>"
    ext = p.suffix.lower()
    if opts.signatures:
        sig = langs.signatures(source, ext)
        if sig is not None:
            source = sig  # sinon : repli silencieux sur le contenu intégral
    return transform(source, ext, opts)


def build_xml(root: Path, files: list[Path], opts: argparse.Namespace) -> str:
    """Sortie XML : balises explicites pour maximiser l'attention du LLM.

    Le contenu n'est PAS échappé (pas de &lt;/&gt;/&amp;) : l'échappement gonflerait
    les tokens sur le code riche en `<`/`>`/`&`. Ce n'est donc pas du XML strictement
    valide, mais les délimiteurs restent sans ambiguïté pour le modèle.
    """
    out = [
        "<file_summary>",
        f"Projet {root.resolve().name} — {len(files)} fichiers, code source intégral. "
        'Chaque fichier est dans une balise <file path="...">…</file>.',
        "</file_summary>",
    ]
    if not opts.no_tree:
        out += ["<directory_structure>", render_tree(root, files),
                "</directory_structure>"]
    out.append("<files>")
    for p in files:
        rel = str(p.relative_to(root)).replace(os.sep, "/")
        out += [f'<file path="{rel}">', render_file(p, opts), "</file>"]
    out.append("</files>")
    if opts.callgraph:
        for name, graph in langs.build_callgraph(files, root) or []:
            out += [f'<call_graph lang="{name}">', graph, "</call_graph>"]
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# Estimation de tokens & presse-papier
# --------------------------------------------------------------------------- #

def estimate_tokens(text: str) -> tuple[int, str]:
    try:
        import tiktoken  # type: ignore
        return len(tiktoken.get_encoding("o200k_base").encode(text)), "tiktoken o200k"
    except Exception:
        return round(len(text) / 4), "approx (chars/4)"


def to_clipboard(text: str) -> bool:
    """Copie vers le presse-papier (WSL clip.exe, wl-copy, xclip, pbcopy)."""
    for cmd in (["clip.exe"], ["wl-copy"], ["xclip", "-selection", "clipboard"],
                ["pbcopy"]):
        try:
            if subprocess.run(cmd, input=text.encode("utf-8"),
                              capture_output=True).returncode == 0:
                return True
        except FileNotFoundError:
            continue
    return False


# --------------------------------------------------------------------------- #
# Bootstrap : .venv local pour activer --signatures/--callgraph sans gérer de venv
# --------------------------------------------------------------------------- #

def _venv_dir() -> Path:
    return Path(__file__).resolve().parent / ".venv"


def _venv_python() -> Path:
    sub = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    return _venv_dir() / sub


def _maybe_reexec(args_list: list[str]) -> None:
    """Si un .venv local existe, relance le script avec SON Python (libs dispo).

    Transparent pour l'utilisateur : il tape toujours `python codetoia.py …`.
    Une variable d'environnement évite toute boucle de relance.
    """
    if os.environ.get("CODETOIA_REEXEC"):
        return
    py = _venv_python()
    try:
        same = py.resolve() == Path(sys.executable).resolve()
    except OSError:
        same = False
    if py.exists() and not same:
        os.environ["CODETOIA_REEXEC"] = "1"
        os.execv(str(py), [str(py), str(Path(__file__).resolve()), *args_list])


def do_setup() -> int:
    """Crée le .venv local et y installe tree-sitter + tiktoken (internet requis)."""
    import venv as _venv
    venv_dir = _venv_dir()
    print(f"→ Création de l'environnement : {venv_dir}", file=sys.stderr)
    try:
        _venv.create(venv_dir, with_pip=True, clear=True)
    except Exception as e:
        print(f"✗ Échec (modules 'venv'/'ensurepip' requis dans Python) : {e}",
              file=sys.stderr)
        return 2
    pkgs = ["tree-sitter", *langs.grammar_packages(), "tiktoken"]
    print(f"→ Installation (internet requis) : {', '.join(pkgs)}", file=sys.stderr)
    r = subprocess.run([str(_venv_python()), "-m", "pip", "install", "-q", *pkgs])
    if r.returncode != 0:
        print("✗ Installation échouée (vérifie la connexion internet).", file=sys.stderr)
        return 1
    print("✓ Setup terminé — `--signatures`/`--callgraph` et tiktoken sont actifs.",
          file=sys.stderr)
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    if "--setup" not in raw:
        _maybe_reexec(raw)  # remplace le process si un .venv local existe

    ap = argparse.ArgumentParser(
        prog="codetoia",
        description="Regroupe les sources d'un dépôt git en un bloc XML pour une IA.",
    )
    ap.add_argument("path", nargs="?", default=".", help="Racine du dépôt (défaut: .)")
    ap.add_argument("-o", "--output", help="Fichier de sortie (sinon: presse-papier)")
    ap.add_argument("--stdout", action="store_true", help="Écrit le dump sur stdout")
    ap.add_argument("-c", "--clipboard", action="store_true",
                    help="Force la copie vers le presse-papier")
    sel = ap.add_mutually_exclusive_group()
    sel.add_argument("--include", metavar="EXT", help="Extensions à inclure (ex: py,ts,md)")
    sel.add_argument("--lang", metavar="LANG",
                     help="N'inclure que les fichiers d'un/des langage(s). "
                          "Abréviations : Go, CS, C, C++, JS, TS, RF (ex: go,cs). "
                          "Exclusif avec --include.")
    ap.add_argument("--exclude", metavar="GLOB", default="",
                    help="Motifs glob à exclure, séparés par des virgules")
    ap.add_argument("--strip-comments", action="store_true", help="Retire les commentaires")
    ap.add_argument("--strip-blank", action="store_true", help="Retire toutes les lignes vides")
    ap.add_argument("--compress", action="store_true",
                    help="Raccourci: --strip-comments --strip-blank")
    ap.add_argument("--signatures", action="store_true",
                    help="Go, CS, C, C++, JS, TS, RF: ne garder que les signatures "
                         "(corps → { ... } / étapes → ...). "
                         "Repli sur le contenu intégral si tree-sitter absent.")
    ap.add_argument("--callgraph", action="store_true",
                    help="Ajoute une section <call_graph> appelant→appelés + index "
                         "inversé (Go, C#, Robot).")
    ap.add_argument("--architecture", action="store_true",
                    help="Raccourci: --signatures + --callgraph.")
    ap.add_argument("--setup", action="store_true",
                    help="Installe (une fois, internet requis) tree-sitter & tiktoken "
                         "dans un .venv local pour activer --signatures/--callgraph. "
                         "Ensuite le script s'en sert automatiquement.")
    ap.add_argument("--no-tree", action="store_true", help="N'inclut pas l'arborescence")
    ap.add_argument("--max-size", type=int, default=512, metavar="KB",
                    help="Ignore les fichiers > KB (défaut: 512, 0 = illimité)")
    ap.add_argument("--keep-empty", action="store_true", help="Garde les fichiers vides")
    args = ap.parse_args(argv)

    if args.setup:
        return do_setup()
    if args.architecture:  # raccourci = signatures + graphe d'appel
        args.signatures = args.callgraph = True
    if args.compress:
        args.strip_comments = args.strip_blank = True
    if args.lang:  # exclusif de --include (garanti par argparse)
        exts, unknown = langs.resolve_filter(args.lang)
        if unknown:
            print(f"Erreur: langage(s) inconnu(s) : {', '.join(unknown)}.\n"
                  f"Abréviations valides : {', '.join(langs.FILTER_NAMES)}.",
                  file=sys.stderr)
            return 2
        args.include = exts
    else:
        args.include = ([e.strip() for e in args.include.split(",")]
                        if args.include else None)
    args.exclude = [e.strip() for e in args.exclude.split(",") if e.strip()]

    root = Path(args.path)
    if not root.is_dir():
        print(f"Erreur: '{root}' n'est pas un répertoire.", file=sys.stderr)
        return 2

    files = collect(root, args)
    if files is None:
        print(f"Erreur: '{root}' n'est pas un dépôt git.", file=sys.stderr)
        return 2
    if not files:
        print("Aucun fichier source trouvé.", file=sys.stderr)
        return 1

    output = build_xml(root, files, args)
    if (args.signatures or args.callgraph) and langs.MISSING:
        miss = ", ".join(sorted(langs.MISSING))
        print(f"⚠ Tree-sitter indisponible pour {miss} (contenu intégral / pas de graphe). "
              f"Active-le une fois avec : python {Path(__file__).name} --setup",
              file=sys.stderr)
    elif args.callgraph and "<call_graph" not in output:
        print(f"ℹ callgraph : généré uniquement pour {', '.join(langs.CALLGRAPH_NAMES)} "
              "(aucun fichier concerné ici).", file=sys.stderr)
    tokens, method = estimate_tokens(output)

    if args.stdout:
        sys.stdout.write(output)
        return 0

    summary = (f"✓ {len(files)} fichiers — {len(output):,} caractères — "
               f"~{tokens:,} tokens ({method})").replace(",", " ")

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"{summary}\n→ écrit dans {args.output}", file=sys.stderr)
        if args.clipboard and to_clipboard(output):
            print("→ copié dans le presse-papier", file=sys.stderr)
    elif to_clipboard(output):
        print(f"{summary}\n→ copié dans le presse-papier", file=sys.stderr)
    else:
        print(f"{summary}\n(presse-papier indisponible — utilise -o ou --stdout)",
              file=sys.stderr)
        sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
