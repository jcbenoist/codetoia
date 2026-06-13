#!/usr/bin/env python3
"""codetoia — regroupe les sources d'un dépôt git en un bloc XML copiable pour une IA.

Objectif : sortie la plus compacte possible (économie de tokens) tout en restant
intégrale et bien structurée pour un LLM (ChatGPT en priorité). La sélection des
fichiers est déléguée à `git ls-files`, donc le .gitignore est respecté exactement.

Usage :
    python codetoia.py .              # tout le dépôt → presse-papier (+ résumé tokens)
    python codetoia.py . -o dump.xml  # → fichier
    python codetoia.py . --compress   # retire commentaires + lignes vides (gain max)
    python codetoia.py . --signatures   # Go/CS/C/C++/JS/TS/RF : signatures seules
    python codetoia.py . --architecture # = --signatures + --callgraph (Go)
    python codetoia.py --setup        # installe (1 fois) les libs tree-sitter/tiktoken
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
# Mode signatures (Tree-sitter) : ne garder que les signatures
# --------------------------------------------------------------------------- #

EXT_LANG = {
    ".go": "go",
    ".cs": "c_sharp",
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".c++": "cpp",
    ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".tsx": "tsx",
    ".robot": "robot", ".resource": "robot",
}

# lang → (module pip importable, fonction renvoyant la grammaire). tree-sitter-typescript
# expose deux grammaires (language_typescript / language_tsx) au lieu de language().
_LANG_GRAMMARS = {
    "go": ("tree_sitter_go", "language"),
    "c_sharp": ("tree_sitter_c_sharp", "language"),
    "c": ("tree_sitter_c", "language"),
    "cpp": ("tree_sitter_cpp", "language"),
    "javascript": ("tree_sitter_javascript", "language"),
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
    "robot": ("tree_sitter_robot", "language"),
}

# Nœuds-fonctions dont on remplace le corps par { ... } ; tout le reste est conservé
# (signatures, types/structs/interfaces, imports, commentaires). Seuls les corps de
# type « bloc » (_BLOCK_BODY_TYPES) sont repliés ; un corps-expression (arrow JS court)
# est laissé tel quel. Robot Framework est traité à part (pas d'accolades).
_JS_CONTAINERS = {
    "function_declaration", "function_expression", "arrow_function",
    "method_definition", "generator_function", "generator_function_declaration",
}
_BODY_CONTAINERS = {
    "go": {"function_declaration", "method_declaration", "func_literal"},
    "c_sharp": {
        "method_declaration", "constructor_declaration", "destructor_declaration",
        "operator_declaration", "conversion_operator_declaration",
        "local_function_statement", "accessor_declaration",
    },
    "c": {"function_definition"},
    "cpp": {"function_definition"},
    "javascript": _JS_CONTAINERS,
    "typescript": _JS_CONTAINERS,
    "tsx": _JS_CONTAINERS,
}

# Corps repliables en { ... } selon la grammaire.
_BLOCK_BODY_TYPES = {"block", "statement_block", "compound_statement"}

_parsers: dict[str, object | None] = {}  # cache (None = grammaire indisponible)
_missing_langs: set[str] = set()


def _load_parser(lang: str):
    """Construit un parser Tree-sitter, robuste aux versions d'API. None si KO."""
    module_name, func_name = _LANG_GRAMMARS[lang]
    try:
        import tree_sitter
        module = importlib.import_module(module_name)
    except Exception:
        return None
    try:
        capsule = getattr(module, func_name)()
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
    for child in node.children:  # repli : bloc, flèche C#
        if child.type in _BLOCK_BODY_TYPES or child.type == "arrow_expression_clause":
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
            if body.type == "arrow_expression_clause":          # C# `=> expr`
                edits.append((body.start_byte, body.end_byte, b"=> ..."))
                return
            if body.type in _BLOCK_BODY_TYPES:                  # corps à accolades
                edits.append((body.start_byte, body.end_byte, b"{ ... }"))
                return
            # corps-expression (arrow JS court `=> x*x`) : on garde, on descend quand même
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
# Arbre d'appel intra-projet (prototype : Go)
# --------------------------------------------------------------------------- #

def _txt(node, data: bytes) -> str:
    return data[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _descend(node, types: set[str]):
    """Itère récursivement les nœuds des types donnés."""
    if node.type in types:
        yield node
    for child in node.children:
        yield from _descend(child, types)


def _go_func_label(node, data: bytes) -> tuple[str, str]:
    """(label affiché, nom simple) pour une fonction/méthode Go."""
    name = node.child_by_field_name("name")
    simple = _txt(name, data) if name else "?"
    recv = node.child_by_field_name("receiver")  # méthode : (e *Engine)
    if recv is not None:
        tid = next(_descend(recv, {"type_identifier"}), None)
        if tid is not None:
            return f"{_txt(tid, data)}.{simple}", simple
    return simple, simple


def _go_callees(body, data: bytes):
    """Noms (simples) des fonctions appelées dans un corps Go."""
    for call in _descend(body, {"call_expression"}):
        fn = call.child_by_field_name("function")
        if fn is None:
            continue
        if fn.type == "identifier":            # foo(...)
            yield _txt(fn, data)
        elif fn.type == "selector_expression":  # e.Close(...), pkg.Query(...)
            field = fn.child_by_field_name("field")
            if field is not None:
                yield _txt(field, data)


# Une entrée = (label affiché, clé d'appariement | None, [clés des appelés]).
# clé None = nœud appelant uniquement (ex. test case Robot, non appelable).
_Func = tuple[str, "str | None", list[str]]


def _field_name(node, data: bytes, fallback: set[str]) -> str:
    n = node.child_by_field_name("name")
    if n is not None:
        return _txt(n, data)
    for c in node.children:
        if c.type in fallback:
            return _txt(c, data)
    return "?"


def _extract_go(tree, data: bytes, qual: str) -> list[_Func]:
    out: list[_Func] = []
    for node in _descend(tree, {"function_declaration", "method_declaration"}):
        rest, simple = _go_func_label(node, data)
        body = node.child_by_field_name("body")
        callees = list(_go_callees(body, data)) if body is not None else []
        out.append((f"{qual}.{rest}", simple, callees))
    return out


def _cs_callees(body, data: bytes):
    for call in _descend(body, {"invocation_expression"}):
        fn = call.child_by_field_name("function")
        if fn is None:
            continue
        if fn.type == "identifier":               # Foo(...)
            yield _txt(fn, data)
        elif fn.type == "member_access_expression":  # x.Foo(...), C.Foo(...)
            name = fn.child_by_field_name("name")
            if name is not None:
                yield _txt(name, data)


def _extract_cs(tree, data: bytes) -> list[_Func]:
    out: list[_Func] = []
    types = {"class_declaration", "struct_declaration", "interface_declaration",
             "record_declaration"}
    funcs = {"method_declaration", "constructor_declaration", "local_function_statement"}

    def walk(node, cls):
        if node.type in types:
            cls = _field_name(node, data, {"identifier"})
            for c in node.children:
                walk(c, cls)
            return
        if node.type in funcs:
            simple = _field_name(node, data, {"identifier"})
            label = f"{cls}.{simple}" if cls else simple
            body = _body_node(node)
            callees = list(_cs_callees(body, data)) if body is not None else []
            out.append((label, simple, callees))
            return
        for c in node.children:
            walk(c, cls)

    walk(tree, None)
    return out


def _rf_norm(name: str) -> str:
    """Robot apparie les keywords insensibles à la casse/espaces/underscores."""
    return name.lower().replace(" ", "").replace("_", "")


def _rf_calls(body, data: bytes):
    for kw in _descend(body, {"keyword"}):                 # invocation directe
        yield _rf_norm(_txt(kw, data))
    for va in _descend(body, {"variable_assignment"}):     # ${x}=  Mon KW  args
        args = next((c for c in va.children if c.type == "arguments"), None)
        if args is not None:
            first = next(iter(args.named_children), None)   # 1er argument = le keyword
            if first is not None:
                yield _rf_norm(_txt(first, data))


def _extract_rf(tree, data: bytes, qual: str) -> list[_Func]:
    out: list[_Func] = []
    for node in _descend(tree, {"keyword_definition", "test_case_definition"}):
        name = _field_name(node, data, {"name"})
        body = next((c for c in node.children if c.type == "body"), None)
        callees = list(_rf_calls(body, data)) if body is not None else []
        # un test case appelle des keywords mais n'est pas appelable lui-même (clé None)
        key = _rf_norm(name) if node.type == "keyword_definition" else None
        out.append((f"{qual}.{name}", key, callees))
    return out


# Langages supportés par le callgraph, et l'extracteur associé.
_CALLGRAPH_LANGS = ("go", "c_sharp", "robot")


def _callgraph_for(lang: str, files: list[Path], root: Path, parser) -> str | None:
    funcs: list[_Func] = []
    name_to_labels: dict[str, set[str]] = {}
    for p in files:
        try:
            data = p.read_text(encoding="utf-8", errors="replace").encode("utf-8")
        except OSError:
            continue
        tree = parser.parse(data).root_node
        if lang == "go":
            items = _extract_go(tree, data, p.parent.name or root.resolve().name)
        elif lang == "c_sharp":
            items = _extract_cs(tree, data)
        else:  # robot
            items = _extract_rf(tree, data, p.stem)
        for label, key, callees in items:
            funcs.append((label, key, callees))
            if key is not None:
                name_to_labels.setdefault(key, set()).add(label)

    declared = set(name_to_labels)
    forward: list[tuple[str, list[str]]] = []
    reverse: dict[str, list[str]] = {}
    for label, key, callees in funcs:
        resolved: list[str] = []
        seen: set[str] = set()
        for c in callees:
            if c in declared and c not in seen and c != key:
                seen.add(c)
                labels = name_to_labels[c]
                resolved.append(next(iter(labels)) if len(labels) == 1 else c)
        if resolved:
            forward.append((label, resolved))
            for r in resolved:
                callers = reverse.setdefault(r, [])
                if label not in callers:
                    callers.append(label)

    if not forward:
        return None
    lines = ["# appelant -> appelés"]
    lines += [f"{label} -> {', '.join(cs)}" for label, cs in forward]
    lines.append("# appelés <- appelants (analyse d'impact)")
    lines += [f"{callee} <- {', '.join(reverse[callee])}" for callee in sorted(reverse)]
    return "\n".join(lines)


def build_callgraph(files: list[Path], root: Path) -> list[tuple[str, str]] | None:
    """Graphes d'appel intra-projet (Go, C#, Robot) : sens direct + index inversé.

    Renvoie une liste (langage, texte) — une section par langage présent — ou None.
    Heuristique syntaxique : appelés appariés par nom simple (normalisé en Robot) ;
    labels qualifiés par dossier (Go), classe (C#) ou fichier (Robot). Un appelé dont
    le nom reste ambigu (plusieurs déclarations) est laissé en nom simple.
    """
    sections: list[tuple[str, str]] = []
    for lang in _CALLGRAPH_LANGS:
        lang_files = [p for p in files if EXT_LANG.get(p.suffix.lower()) == lang]
        if not lang_files:
            continue
        parser = _get_parser(lang)
        if parser is None:
            continue
        graph = _callgraph_for(lang, lang_files, root, parser)
        if graph:
            sections.append((lang, graph))
    return sections or None


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
    if opts.callgraph:
        for lang, graph in build_callgraph(files, root) or []:
            out += [f'<call_graph lang="{lang}">', graph, "</call_graph>"]
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
    pkgs = ["tree-sitter", "tree-sitter-go", "tree-sitter-c-sharp", "tree-sitter-c",
            "tree-sitter-cpp", "tree-sitter-javascript", "tree-sitter-typescript",
            "tree-sitter-robot", "tiktoken"]
    print(f"→ Installation (internet requis) : {', '.join(pkgs)}", file=sys.stderr)
    r = subprocess.run([str(_venv_python()), "-m", "pip", "install", "-q", *pkgs])
    if r.returncode != 0:
        print("✗ Installation échouée (vérifie la connexion internet).", file=sys.stderr)
        return 1
    print("✓ Setup terminé — `--signatures`/`--callgraph` et tiktoken sont actifs.",
          file=sys.stderr)
    return 0


# --------------------------------------------------------------------------- #
# Filtrage par langage (--lang, exclusif de --include)
# --------------------------------------------------------------------------- #

# Langage → extensions. Noms canoniques = abréviations (mêmes langages que --signatures).
LANG_EXTS = {
    "go":  [".go"],
    "cs":  [".cs"],
    "c":   [".c", ".h"],
    "c++": [".cpp", ".cc", ".cxx", ".c++", ".hpp", ".hh", ".hxx", ".h"],
    "js":  [".js", ".jsx", ".mjs", ".cjs"],
    "ts":  [".ts", ".mts", ".cts", ".tsx"],
    "rf":  [".robot", ".resource"],
}
# Noms longs tolérés en entrée → abréviation canonique.
LANG_ALIASES = {
    "golang": "go", "c#": "cs", "csharp": "cs",
    "cpp": "c++", "cxx": "c++", "cc": "c++",
    "javascript": "js", "node": "js", "typescript": "ts",
    "robot": "rf", "robotframework": "rf",
}


def _resolve_langs(spec: str) -> list[str] | None:
    """Convertit 'go,cs' en liste d'extensions. None (+ message) si langage inconnu."""
    exts: list[str] = []
    unknown: list[str] = []
    for raw in spec.split(","):
        name = raw.strip().lower()
        if not name:
            continue
        name = LANG_ALIASES.get(name, name)
        if name in LANG_EXTS:
            exts += LANG_EXTS[name]
        else:
            unknown.append(raw.strip())
    if unknown:
        print(f"Erreur: langage(s) inconnu(s) : {', '.join(unknown)}.\n"
              f"Abréviations valides : {', '.join(LANG_EXTS)}.", file=sys.stderr)
        return None
    return exts


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
        args.include = _resolve_langs(args.lang)
        if args.include is None:
            return 2
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
    if (args.signatures or args.callgraph) and _missing_langs:
        miss = ", ".join(sorted(_missing_langs))
        print(f"⚠ Tree-sitter indisponible pour {miss} (contenu intégral / pas de graphe). "
              f"Active-le une fois avec : python {Path(__file__).name} --setup",
              file=sys.stderr)
    elif args.callgraph and "<call_graph" not in output:
        print("ℹ callgraph : généré uniquement pour Go, C# et Robot "
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
