#!/usr/bin/env python3
"""codetoia — regroupe les sources d'un dépôt git en un bloc XML copiable pour une IA.

Objectif : sortie la plus compacte possible (économie de tokens) tout en restant
intégrale et bien structurée pour un LLM (ChatGPT en priorité). La sélection des
fichiers est déléguée à `git ls-files`, donc le .gitignore est respecté exactement.

Usage :
    python codetoia.py .              # tout le dépôt → presse-papier (+ résumé tokens)
    python codetoia.py . -o dump.xml  # → fichier
    python codetoia.py . --compress   # retire commentaires + lignes vides (gain max)
    python codetoia.py . --signatures # Go, C#, C, C++, Robot : signatures seules
    python codetoia.py --setup        # installe (1 fois) les libs de --signatures
"""
from __future__ import annotations

import argparse
import fnmatch
import importlib
import os
import re
import subprocess
import sys
from pathlib import Path

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

# Marqueurs de commentaires par extension (pour --strip-comments / --compress).
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
    un '#'/'/'/' situé à l'intérieur d'une chaîne.
    """
    block = BLOCK_COMMENT.get(ext)
    if block:
        opener, closer = re.escape(block[0]), re.escape(block[1])
        text = re.sub(opener + r".*?" + closer, "", text, flags=re.DOTALL)

    tok = LINE_COMMENT.get(ext)
    if tok:
        out = []
        for line in text.splitlines():
            if line.lstrip().startswith(tok):
                continue
            if tok in line and not any(q in line for q in "\"'`"):
                line = line[: line.index(tok)].rstrip()
            out.append(line)
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
# Mode signatures (Tree-sitter) — Go & C#
# --------------------------------------------------------------------------- #

EXT_LANG = {
    ".go": "go",
    ".cs": "c_sharp",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".c++": "cpp",
    ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp",
    ".robot": "robot", ".resource": "robot",
}
_LANG_MODULES = {
    "go": "tree_sitter_go", "c_sharp": "tree_sitter_c_sharp",
    "c": "tree_sitter_c", "cpp": "tree_sitter_cpp", "robot": "tree_sitter_robot",
}

# Langages « à accolades » : on remplace le corps de ces nœuds par { ... } ; tout le
# reste est conservé (signatures, types/structs/interfaces, imports, commentaires).
# Robot Framework est traité à part dans _collect_bodies (pas d'accolades).
_BODY_CONTAINERS = {
    "go": {"function_declaration", "method_declaration", "func_literal"},
    "c_sharp": {
        "method_declaration", "constructor_declaration", "destructor_declaration",
        "operator_declaration", "conversion_operator_declaration",
        "local_function_statement", "accessor_declaration",
    },
    "c": {"function_definition"},
    "cpp": {"function_definition"},
}

_parsers: dict[str, object | None] = {}  # cache (None = grammaire indisponible)
_missing_langs: set[str] = set()


def _load_parser(lang: str):
    """Construit un parser Tree-sitter, robuste aux versions d'API. None si KO."""
    try:
        import tree_sitter
        module = importlib.import_module(_LANG_MODULES[lang])
    except Exception:
        return None
    try:
        capsule = module.language()
        try:
            language = tree_sitter.Language(capsule)        # tree_sitter >= 0.22
        except TypeError:
            language = tree_sitter.Language(capsule, lang)   # tree_sitter 0.21
        try:
            return tree_sitter.Parser(language)              # API récente
        except TypeError:
            parser = tree_sitter.Parser()                    # API ancienne
            try:
                parser.language = language
            except (AttributeError, TypeError):
                parser.set_language(language)
            return parser
    except Exception:
        return None


def _get_parser(lang: str):
    if lang not in _parsers:
        _parsers[lang] = _load_parser(lang)
        if _parsers[lang] is None:
            _missing_langs.add(lang)
    return _parsers[lang]


def _body_node(node):
    body = node.child_by_field_name("body")
    if body is not None:
        return body
    for child in node.children:  # repli : block (méthode), flèche C#, ou C/C++
        if child.type in ("block", "arrow_expression_clause", "compound_statement"):
            return child
    return None


def _collect_robot_bodies(node, edits: list) -> None:
    """Robot Framework : garde le nom + les [Settings], remplace les étapes par `...`."""
    if node.type in ("keyword_definition", "test_case_definition"):
        body = next((c for c in node.children if c.type == "body"), None)
        if body is not None:
            steps = [c for c in body.children if c.type == "statement"]
            if steps:
                edits.append((steps[0].start_byte, steps[-1].end_byte, b"..."))
        return
    for child in node.children:
        _collect_robot_bodies(child, edits)


def _collect_bodies(node, lang: str, edits: list) -> None:
    if lang == "robot":
        _collect_robot_bodies(node, edits)
        return
    if node.type in _BODY_CONTAINERS[lang]:
        body = _body_node(node)
        if body is not None and body.end_byte > body.start_byte:
            rep = b"=> ..." if body.type == "arrow_expression_clause" else b"{ ... }"
            edits.append((body.start_byte, body.end_byte, rep))
            return  # ne pas descendre dans le corps qu'on vient de remplacer
    for child in node.children:
        _collect_bodies(child, lang, edits)


def to_signatures(source: str, lang: str) -> str | None:
    """Garde signatures + types, remplace les corps de fonctions par `{ ... }`.

    None si Tree-sitter ou la grammaire est indisponible (→ repli appelant).
    """
    parser = _get_parser(lang)
    if parser is None:
        return None
    data = source.encode("utf-8")
    edits: list[tuple[int, int, bytes]] = []
    _collect_bodies(parser.parse(data).root_node, lang, edits)
    if not edits:
        return source
    edits.sort()
    out, pos = bytearray(), 0
    for start, end, rep in edits:
        out += data[pos:start] + rep
        pos = end
    out += data[pos:]
    return out.decode("utf-8", "replace")


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
    return "\n".join(out) + "\n"


def render_file(p: Path, opts: argparse.Namespace) -> str:
    try:
        source = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"<illisible: {e}>"
    ext = p.suffix.lower()
    if opts.signatures and ext in EXT_LANG:
        sig = to_signatures(source, EXT_LANG[ext])
        if sig is not None:
            source = sig  # sinon : repli silencieux sur le contenu intégral
    return transform(source, ext, opts)


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
# Bootstrap : .venv local pour activer --signatures sans gérer de venv soi-même
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
    pkgs = ["tree-sitter", "tree-sitter-go", "tree-sitter-c-sharp", "tree-sitter-c",
            "tree-sitter-cpp", "tree-sitter-robot", "tiktoken"]
    print(f"→ Installation (internet requis) : {', '.join(pkgs)}", file=sys.stderr)
    r = subprocess.run([str(_venv_python()), "-m", "pip", "install", "-q", *pkgs])
    if r.returncode != 0:
        print("✗ Installation échouée (vérifie la connexion internet).", file=sys.stderr)
        return 1
    print("✓ Setup terminé — `--signatures` et le comptage tiktoken sont actifs.",
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
    ap.add_argument("--include", metavar="EXT", help="Extensions à inclure (ex: py,ts,md)")
    ap.add_argument("--exclude", metavar="GLOB", default="",
                    help="Motifs glob à exclure, séparés par des virgules")
    ap.add_argument("--strip-comments", action="store_true", help="Retire les commentaires")
    ap.add_argument("--strip-blank", action="store_true", help="Retire toutes les lignes vides")
    ap.add_argument("--compress", action="store_true",
                    help="Raccourci: --strip-comments --strip-blank")
    ap.add_argument("--signatures", action="store_true",
                    help="Go, C#, C, C++, Robot: ne garder que les signatures "
                         "(corps → { ... } / étapes → ...). "
                         "Repli sur le contenu intégral si tree-sitter absent.")
    ap.add_argument("--setup", action="store_true",
                    help="Installe (une fois, internet requis) tree-sitter & tiktoken "
                         "dans un .venv local pour activer --signatures. Ensuite le "
                         "script s'en sert automatiquement.")
    ap.add_argument("--no-tree", action="store_true", help="N'inclut pas l'arborescence")
    ap.add_argument("--max-size", type=int, default=512, metavar="KB",
                    help="Ignore les fichiers > KB (défaut: 512, 0 = illimité)")
    ap.add_argument("--keep-empty", action="store_true", help="Garde les fichiers vides")
    args = ap.parse_args(argv)

    if args.setup:
        return do_setup()
    if args.compress:
        args.strip_comments = args.strip_blank = True
    args.include = [e.strip() for e in args.include.split(",")] if args.include else None
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
    if args.signatures and _missing_langs:
        miss = ", ".join(sorted(_missing_langs))
        print(f"⚠ Mode signatures indisponible pour {miss} (contenu intégral conservé). "
              f"Active-le une fois avec : python {Path(__file__).name} --setup",
              file=sys.stderr)
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
