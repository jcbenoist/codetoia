#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 jcb — développé avec l'assistance de Claude (Anthropic).
"""codetoia — regroupe les sources d'un dépôt git en un bloc XML copiable pour une IA.

Objectif : sortie la plus compacte possible (économie de tokens) tout en restant
intégrale et bien structurée pour un LLM (ChatGPT en priorité). La sélection des
fichiers est déléguée à `git ls-files`, donc le .gitignore est respecté exactement.

Tout ce qui est spécifique à un langage (grammaire, signatures, graphe d'appel,
commentaires) vit dans le package `codetoia_langs/` (un module par langage).

Usage :
    python codetoia.py .              # → fichier <repo>-dump.xml dans le dossier courant
    python codetoia.py . -o dump.xml  # → fichier nommé explicitement
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
# Factorisation des commentaires redondants (--dedup-comments)
# --------------------------------------------------------------------------- #

_DEDUP_MIN_LEN = 60      # « substance » minimale d'un fragment (somme des lignes normalisées)
_DEDUP_MIN_FILES = 2     # redondant = au moins N occurrences (fichiers ou répétitions)


def _norm_line(line: str, lc: str | None, block) -> str:
    """Texte d'une ligne de commentaire, sans marqueurs ni marge « * »."""
    s = line.strip()
    if block:
        s = s.replace(block[0], "").replace(block[1], "")
    if lc and s.startswith(lc):
        s = s[len(lc):]
    return s.strip().lstrip("*").strip()


def _comment_line_map(text: str, lc: str | None, block):
    """(lignes, info) où info[i]=(normalisé, style) si la ligne i est un commentaire
    factorisable, sinon None. Les délimiteurs `/*` et `*/` sont exclus (préservés)."""
    lines = text.split("\n")
    n, i = len(lines), 0
    info: list = [None] * n
    while i < n:
        s = lines[i].strip()
        if lc and s.startswith(lc):                       # run de commentaires ligne
            j = i
            while j < n and lines[j].strip().startswith(lc):
                info[j] = (_norm_line(lines[j], lc, None), "line")
                j += 1
            i = j
        elif block and s.startswith(block[0]):            # bloc /* ... */
            j = i
            while j < n and block[1] not in lines[j]:
                j += 1
            end = min(j + 1, n)
            for k in range(i + 1, end - 1):               # intérieur seulement
                info[k] = (_norm_line(lines[k], None, block), "block")
            i = end
        else:
            i += 1
    return lines, info


_DEDUP_SEED_MIN = 12     # longueur mini d'une ligne « ancre » distinctive


def scan_comment_blocks(files: list[Path]):
    """Analyse globale → (blocs communs [(id, brut)], plan {fichier: [(début,fin,réf)]}).

    Ancre + extension par unanimité : une ligne distinctive partagée sert d'ancre, et
    on étend à gauche/droite tant que TOUTES ses occurrences ont la même ligne — ce qui
    capture le plus grand bloc commun (ex. pavé de licence) en s'arrêtant pile sur les
    lignes qui varient (description, copyright propre au fichier). Les plus longs blocs
    sont traités en premier ; les lignes déjà prises ne sont pas réutilisées.
    """
    flines: dict = {}   # path -> lignes
    finfo: dict = {}    # path -> info[i] = (normalisé, style) | None
    flc: dict = {}      # path -> marqueur ligne
    occ: dict = {}      # normalisé -> [(path, idx)]
    nfiles: dict = {}   # normalisé -> set(path)
    for p in files:
        lc, block = langs.comment_markers(p.suffix.lower())
        if not lc and not block:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines, info = _comment_line_map(text, lc, block)
        sp = str(p)
        flines[sp], finfo[sp], flc[sp] = lines, info, lc
        for i, it in enumerate(info):
            if it is not None:
                occ.setdefault(it[0], []).append((sp, i))
                nfiles.setdefault(it[0], set()).add(sp)

    covered: set = set()  # (path, idx) déjà factorisés

    def line_at(f, k):
        if k < 0 or k >= len(finfo[f]) or finfo[f][k] is None or (f, k) in covered:
            return None
        return finfo[f][k][0]

    common: list = []
    plan: dict = {}
    seeds = sorted((s for s, fs in nfiles.items()
                    if len(fs) >= _DEDUP_MIN_FILES and len(s) >= _DEDUP_SEED_MIN),
                   key=len, reverse=True)
    for seed in seeds:
        occs = [(f, i) for (f, i) in occ[seed] if (f, i) not in covered]
        if len({f for f, _ in occs}) < _DEDUP_MIN_FILES:
            continue
        f0, i0 = occs[0]
        lo = hi = 0
        while True:                                   # étend à gauche
            v = line_at(f0, i0 + lo - 1)
            if v is None or any(line_at(f, i + lo - 1) != v for f, i in occs):
                break
            lo -= 1
        while True:                                   # étend à droite
            v = line_at(f0, i0 + hi + 1)
            if v is None or any(line_at(f, i + hi + 1) != v for f, i in occs):
                break
            hi += 1
        seq = [finfo[f0][i0 + d][0] for d in range(lo, hi + 1)]
        if sum(len(s) for s in seq) < _DEDUP_MIN_LEN:
            continue
        raw0 = "\n".join(flines[f0][i0 + lo: i0 + hi + 1])
        total = sum(len("\n".join(flines[f][i + lo: i + hi + 1])) for f, i in occs)
        if total - (len(raw0) + 14 + 16 * len(occs)) <= 0:   # garde-fou gain net
            continue
        cid = len(common) + 1
        common.append((cid, raw0))
        for f, i in occs:
            start, end = i + lo, i + hi + 1
            ln0 = flines[f][start]
            indent = ln0[:len(ln0) - len(ln0.lstrip())]
            prefix = indent + ("* " if finfo[f][start][1] == "block" else f"{flc[f]} ")
            plan.setdefault(f, []).append((start, end, f"{prefix}[common-{cid}]"))
            covered.update((f, k) for k in range(start, end))
    return common, plan


def factor_comments(text: str, plan_list: list) -> str:
    """Applique le plan de remplacement (du bas vers le haut pour garder les index)."""
    lines = text.split("\n")
    for start, end, ref in sorted(plan_list, key=lambda x: x[0], reverse=True):
        lines[start:end] = [ref]
    return "\n".join(lines)


def render_common(common: list) -> str:
    return "\n\n".join(f"[common-{cid}]\n{raw}" for cid, raw in common)


# --------------------------------------------------------------------------- #
# Masquage de secrets (--mask-secrets) : détection regex haute confiance
# --------------------------------------------------------------------------- #

_REDACTED = "[redacted]"

# (nom, motif, groupe à masquer). groupe 0 = tout le match ; sinon on ne masque
# que la valeur capturée (on garde la clé/le contexte autour).
_SECRET_RULES = [
    ("clé-privée", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL), 0),
    ("aws-access-key-id", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), 0),
    ("github-token", re.compile(r"\bgh[opsru]_[A-Za-z0-9]{36,}\b"), 0),
    ("github-pat", re.compile(r"\bgithub_pat_[0-9A-Za-z_]{60,}\b"), 0),
    ("slack-token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"), 0),
    ("slack-webhook", re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/_-]+"), 0),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), 0),
    ("stripe-clé-live", re.compile(r"\b[sr]k_live_[0-9a-zA-Z]{20,}\b"), 0),
    ("jwt", re.compile(
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), 0),
    ("secret-assigné", re.compile(
        r"""(?i)\b(passwd|password|secret|api[_-]?key|access[_-]?token|auth[_-]?token"""
        r"""|client[_-]?secret|private[_-]?key)\b\s*[:=]\s*(['"])([^'"\n]{6,})\2"""), 3),
]

_SECRET_HITS: list[tuple[str, str]] = []  # (fichier, type) accumulés sur le run


def mask_secrets(text: str, rel: str) -> str:
    """Remplace les secrets détectés par [redacted] et enregistre les trouvailles."""
    for name, rx, grp in _SECRET_RULES:
        def repl(m, name=name, grp=grp):
            _SECRET_HITS.append((rel, name))
            if grp == 0:
                return _REDACTED
            whole = m.group(0)
            a, b = m.start(grp) - m.start(0), m.end(grp) - m.start(0)
            return whole[:a] + _REDACTED + whole[b:]
        text = rx.sub(repl, text)
    return text


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


def render_file(p: Path, rel: str, opts: argparse.Namespace, plan: dict) -> str:
    try:
        source = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"<illisible: {e}>"
    ext = p.suffix.lower()
    pl = plan.get(str(p))
    if pl:
        source = factor_comments(source, pl)  # commentaires redondants → [common-N]
    if opts.mask_secrets:
        source = mask_secrets(source, rel)  # avant tout traitement
    if opts.signatures:
        sig = langs.signatures(source, ext)
        if sig is not None:
            source = sig  # sinon : repli silencieux sur le contenu intégral
    return transform(source, ext, opts)


# --------------------------------------------------------------------------- #
# Diff git (--diff A-B) — A/B = sha/tag/branche, séparateur '-' désambiguïsé via git
# --------------------------------------------------------------------------- #

def _is_ref(root: Path, ref: str) -> bool:
    """Vrai si `ref` résout vers un commit (branche → son dernier commit, tag, sha)."""
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        capture_output=True).returncode == 0


def resolve_diff(root: Path, spec: str):
    """(label_de, label_à, args git diff) ou None (+ message) si spec invalide.

    spec : "" (modifs non commitées) ; "A..B"/"A...B" (syntaxe git) ; ou "A-B" où A et B
    sont des sha/tag/branche. Le '-' est ambigu (les refs peuvent en contenir) : on
    valide chaque découpe via git et on lève l'ambiguïté si plusieurs sont valides.
    """
    if spec == "":
        return "HEAD", "(copie de travail)", ["HEAD"]
    if ".." in spec:                                   # syntaxe git native, non ambiguë
        sep = "..." if "..." in spec else ".."
        a, _, b = spec.partition(sep)
        return a or "HEAD", b or "(copie de travail)", [spec]
    valid = [(spec[:i], spec[i + 1:]) for i, c in enumerate(spec)
             if c == "-" and spec[:i] and spec[i + 1:]
             and _is_ref(root, spec[:i]) and _is_ref(root, spec[i + 1:])]
    if len(valid) == 1:
        a, b = valid[0]
        return a, b, [a, b]
    if len(valid) > 1:
        choices = ", ".join(f"{a}↔{b}" for a, b in valid)
        print(f"Erreur: --diff '{spec}' est ambigu ({choices}). "
              "Utilise la forme A..B pour trancher.", file=sys.stderr)
        return None
    if _is_ref(root, spec):                            # réf seule → réf ↔ copie de travail
        return spec, "(copie de travail)", [spec]
    print(f"Erreur: --diff '{spec}' : référence(s) git introuvable(s). "
          "Attendu <ref>-<ref> (sha/tag/branche) ou A..B.", file=sys.stderr)
    return None


def git_diff(root: Path, spec: str):
    """(label_de, label_à, texte du diff) ou None si spec invalide."""
    resolved = resolve_diff(root, spec)
    if resolved is None:
        return None
    frm, to, args = resolved
    proc = subprocess.run(["git", "-C", str(root), "diff", *args], capture_output=True)
    return frm, to, proc.stdout.decode("utf-8", "replace")


def build_prompt(opts: argparse.Namespace) -> str:
    """Bloc <prompt> XML cadrant le LLM en ingénieur logiciel + légende des conventions.

    Deux cadrages : dump complet du dépôt (défaut) ; ou message de suivi ne contenant
    QUE le diff (`--diff`), à envoyer après le dump complet dans la même conversation.
    """
    if opts.diff is not None:               # message de suivi : seulement les changements
        notes = ["« git_diff » contient un diff git unifié des changements à examiner "
                 "(lignes « + » ajoutées, « - » retirées)."]
        if opts.mask_secrets:
            notes.append("« [redacted] » remplace un secret masqué.")
        context = ("Le dépôt de code complet t'a déjà été fourni dans cette conversation. "
                   "Ce message n'apporte QUE les changements survenus depuis : un diff git "
                   "(section « git_diff »). Des questions d'ingénieur sur ces changements — "
                   "revue, correction, impact — vont suivre.")
        instructions = ("Rapporte ces changements au code déjà fourni. Évalue leur "
                        "correction, leurs effets de bord et leur impact sur le reste du "
                        "projet (appuie-toi sur le call_graph déjà transmis si besoin). Cite "
                        "les fichiers par leur chemin ; si une information n'y figure pas, "
                        "dis-le, n'invente rien. Attends les questions.")
    else:                                   # dump complet du dépôt
        notes = ["Le code est dans la section « files » : une entrée par fichier, identifiée "
                 "par son attribut path ; « directory_structure » donne l'arborescence."]
        if opts.signatures:
            notes.append("Un corps remplacé par « { ... } » (étapes Robot par « ... ») signifie "
                         "que seule la signature est conservée (implémentation masquée).")
        if opts.callgraph:
            notes.append("« call_graph » liste les appels internes au projet : « appelant -> "
                         "appelés », puis l'index inverse « appelés <- appelants » (analyse d'impact).")
        if opts.dedup_comments:
            notes.append("Les marqueurs « [common-N] » dans les fichiers renvoient à la section "
                         "« common_comments » (blocs de commentaires partagés, factorisés).")
        if opts.mask_secrets:
            notes.append("« [redacted] » remplace un secret masqué.")
        context = ("Le dépôt de code complet est fourni ci-dessous. Des questions "
                   "d'ingénieur précises sur ce code vont suivre.")
        instructions = ("Analyse le code pour pouvoir y répondre avec exactitude. Cite les "
                        "fichiers par leur chemin ; si une information n'y figure pas, dis-le, "
                        "n'invente rien. Attends les questions.")
    return (
        "<prompt>\n"
        "<role>Tu es un ingénieur logiciel senior.</role>\n"
        f"<context>{context}</context>\n"
        "<format_notes>" + " ".join(notes) + "</format_notes>\n"
        f"<instructions>{instructions}</instructions>\n"
        "</prompt>"
    )


def build_diff_xml(opts: argparse.Namespace, diff) -> str:
    """Message de suivi autonome : <prompt> recadré + le seul <git_diff>.

    Le dépôt complet est censé avoir été envoyé dans un premier message ; ici on
    n'émet que les changements, à coller à la suite dans la même conversation.
    """
    out = []
    if opts.prompt:
        out.append(build_prompt(opts))
    frm, to, text = diff
    if opts.mask_secrets:
        text = mask_secrets(text, "git_diff")
    out += [f'<git_diff from="{frm}" to="{to}">',
            text.strip() or "(aucune différence)", "</git_diff>"]
    return "\n".join(out) + "\n"


def build_xml(root: Path, files: list[Path], opts: argparse.Namespace,
              common: list, plan: dict) -> str:
    """Sortie XML : balises explicites pour maximiser l'attention du LLM.

    Le contenu n'est PAS échappé (pas de &lt;/&gt;/&amp;) : l'échappement gonflerait
    les tokens sur le code riche en `<`/`>`/`&`. Ce n'est donc pas du XML strictement
    valide, mais les délimiteurs restent sans ambiguïté pour le modèle.
    """
    out = []
    if opts.prompt:                       # cadre le LLM en tête du dump (défaut)
        out.append(build_prompt(opts))
    out += [
        "<file_summary>",
        f"Projet {root.resolve().name} — {len(files)} fichiers, code source intégral. "
        'Chaque fichier est dans une balise <file path="...">…</file>.',
        "</file_summary>",
    ]
    if not opts.no_tree:
        out += ["<directory_structure>", render_tree(root, files),
                "</directory_structure>"]
    if common:
        out += ["<common_comments>",
                "# Blocs de commentaires partagés ; les fichiers y réfèrent par [common-N].",
                render_common(common), "</common_comments>"]
    out.append("<files>")
    for p in files:
        rel = str(p.relative_to(root)).replace(os.sep, "/")
        out += [f'<file path="{rel}">', render_file(p, rel, opts, plan), "</file>"]
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


def output_name(root: Path, args: argparse.Namespace) -> str:
    """Nom de fichier par défaut : <repo>-<options>-dump.xml (options rappelées)."""
    parts = [root.resolve().name]
    if args.diff is not None:                  # diff seul : pas d'option de contenu du dépôt
        parts.append("diff" + (f"-{args.diff}" if args.diff else ""))
        if not args.mask_secrets:
            parts.append("nomask")
        if not args.prompt:
            parts.append("noprompt")
        safe = [s for s in (re.sub(r"[^A-Za-z0-9]+", "-", p).strip("-") for p in parts) if s]
        return "-".join(safe) + "-dump.xml"
    if args.architecture:
        parts.append("architecture")
    else:
        if args.signatures:
            parts.append("signatures")
        if args.callgraph:
            parts.append("callgraph")
    if args.compress:
        parts.append("compress")
    else:
        if args.strip_comments:
            parts.append("nocomments")
        if args.strip_blank:
            parts.append("noblank")
    if args.lang:
        parts.append(args.lang)
    elif args.include:
        parts.append("inc-" + ",".join(args.include))
    if not args.mask_secrets:
        parts.append("nomask")
    if not args.dedup_comments:
        parts.append("nodedup")
    if not args.prompt:
        parts.append("noprompt")
    if args.no_tree:
        parts.append("notree")
    safe = [s for s in (re.sub(r"[^A-Za-z0-9]+", "-", p).strip("-") for p in parts) if s]
    return "-".join(safe) + "-dump.xml"


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

README_HELP_BEGIN = "<!-- BEGIN: codetoia --help (généré par `codetoia --readme`, ne pas éditer) -->"
README_HELP_END = "<!-- END: codetoia --help -->"


def build_parser() -> argparse.ArgumentParser:
    """Construit l'ArgumentParser — source de vérité unique de la CLI.

    Le bloc « Utilisation » du README est régénéré depuis ce parser via `--readme`
    (voir update_readme) : le --help reste la référence, la doc ne peut pas diverger.
    """
    ap = argparse.ArgumentParser(
        prog="codetoia",
        description="Regroupe les sources d'un dépôt git en un bloc XML pour une IA.",
    )
    ap.add_argument("path", nargs="?", default=".", help="Racine du dépôt (défaut: .)")
    ap.add_argument("-o", "--output",
                    help="Fichier de sortie (défaut: <repo>-<options>-dump.xml "
                         "dans le répertoire courant)")
    ap.add_argument("--stdout", action="store_true", help="Écrit le dump sur stdout")
    ap.add_argument("-c", "--clipboard", action="store_true",
                    help="Copie aussi vers le presse-papier (en plus du fichier)")
    sel = ap.add_mutually_exclusive_group()
    sel.add_argument("--include", metavar="EXT", help="Extensions à inclure (ex: py,ts,md)")
    sel.add_argument("--lang", metavar="LANG",
                     help="N'inclure que les fichiers d'un/des langage(s). "
                          "Abréviations : Go, CS, C, C++, JS, TS, RF (ex: go,cs). "
                          "Exclusif avec --include.")
    ap.add_argument("--exclude", metavar="GLOB", default="",
                    help="Motifs glob à exclure, séparés par des virgules")
    ap.add_argument("--diff", nargs="?", const="", default=None, metavar="A-B",
                    help="Message de suivi ne contenant QUE le diff (à coller après le "
                         "dump complet, dans la même conversation). Sans arg : modifs non "
                         "commitées. A-B : entre deux réfs sha/tag/branche (ex: "
                         "main-feature, v1.0-v2.0) ; A..B accepté aussi pour lever toute "
                         "ambiguïté.")
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
                         "inversé (Go, C#, C, C++, JS, TS, Robot).")
    ap.add_argument("--architecture", action="store_true",
                    help="Raccourci: --signatures + --callgraph.")
    ap.add_argument("--setup", action="store_true",
                    help="Installe (une fois, internet requis) tree-sitter & tiktoken "
                         "dans un .venv local pour activer --signatures/--callgraph. "
                         "Ensuite le script s'en sert automatiquement.")
    ap.add_argument("--no-mask-secrets", dest="mask_secrets", action="store_false",
                    help="Désactive le masquage des secrets (actif par défaut : clés "
                         "privées, tokens, secret=\"...\" → [redacted]).")
    ap.add_argument("--no-dedup-comments", dest="dedup_comments", action="store_false",
                    help="Désactive la factorisation des blocs de commentaires répétés "
                         "(active par défaut, avec garde-fou anti-augmentation).")
    ap.add_argument("--no-prompt", dest="prompt", action="store_false",
                    help="N'inclut pas le <prompt> d'instruction en tête (actif par "
                         "défaut : cadre le LLM en ingénieur, légende des conventions).")
    ap.add_argument("--no-tree", action="store_true", help="N'inclut pas l'arborescence")
    ap.add_argument("--max-size", type=int, default=512, metavar="KB",
                    help="Ignore les fichiers > KB (défaut: 512, 0 = illimité)")
    ap.add_argument("--keep-empty", action="store_true", help="Garde les fichiers vides")
    ap.add_argument("--readme", action="store_true", help=argparse.SUPPRESS)
    return ap


def update_readme(ap: argparse.ArgumentParser) -> int:
    """Réinjecte le --help courant dans le README, entre les marqueurs (idempotent)."""
    readme = Path(__file__).resolve().parent / "README.md"
    os.environ["COLUMNS"] = "80"          # rendu déterministe, indépendant du terminal
    block = (f"{README_HELP_BEGIN}\n```text\n{ap.format_help().rstrip()}\n```\n"
             f"{README_HELP_END}")
    try:
        text = readme.read_text(encoding="utf-8")
    except OSError as e:
        print(f"Erreur: lecture de {readme} impossible : {e}", file=sys.stderr)
        return 2
    pat = re.compile(re.escape(README_HELP_BEGIN) + r".*?" + re.escape(README_HELP_END),
                     re.DOTALL)
    if not pat.search(text):
        print(f"Erreur: marqueurs absents de {readme.name} "
              f"(attendu {README_HELP_BEGIN} … {README_HELP_END}).", file=sys.stderr)
        return 2
    new = pat.sub(lambda _: block, text)
    if new == text:
        print(f"✓ {readme.name} : section --help déjà à jour.", file=sys.stderr)
        return 0
    readme.write_text(new, encoding="utf-8")
    print(f"✓ {readme.name} : section --help régénérée depuis la CLI.", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    if not {"--setup", "--readme"} & set(raw):
        _maybe_reexec(raw)  # remplace le process si un .venv local existe

    ap = build_parser()
    args = ap.parse_args(argv)

    if args.readme:
        return update_readme(ap)
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

    # Mode diff seul : la sortie ne contient QUE le diff (le dépôt complet a déjà été
    # envoyé dans un premier message). On ne collecte donc pas les fichiers du dépôt.
    if args.diff is not None:
        if subprocess.run(["git", "-C", str(root), "rev-parse", "--git-dir"],
                          capture_output=True).returncode != 0:
            print(f"Erreur: '{root}' n'est pas un dépôt git.", file=sys.stderr)
            return 2
        diff = git_diff(root, args.diff)
        if diff is None:
            return 2
        _SECRET_HITS.clear()
        output = build_diff_xml(args, diff)
        return _emit(args, root, output, head="✓ diff seul")

    files = collect(root, args)
    if files is None:
        print(f"Erreur: '{root}' n'est pas un dépôt git.", file=sys.stderr)
        return 2
    if not files:
        print("Aucun fichier source trouvé.", file=sys.stderr)
        return 1

    _SECRET_HITS.clear()
    # --dedup-comments : sans objet si on retire déjà tous les commentaires
    common, plan = (scan_comment_blocks(files)
                    if args.dedup_comments and not args.strip_comments else ([], {}))
    if common:
        print(f"🧩 {len(common)} bloc(s) de commentaires factorisé(s) dans "
              "<common_comments>", file=sys.stderr)
    output = build_xml(root, files, args, common, plan)
    if (args.signatures or args.callgraph) and langs.MISSING:
        miss = ", ".join(sorted(langs.MISSING))
        print(f"⚠ Tree-sitter indisponible pour {miss} (contenu intégral / pas de graphe). "
              f"Active-le une fois avec : python {Path(__file__).name} --setup",
              file=sys.stderr)
    elif args.callgraph and "<call_graph" not in output:
        print(f"ℹ callgraph : généré uniquement pour {', '.join(langs.CALLGRAPH_NAMES)} "
              "(aucun fichier concerné ici).", file=sys.stderr)
    return _emit(args, root, output, head=f"✓ {len(files)} fichiers")


def _emit(args: argparse.Namespace, root: Path, output: str, head: str) -> int:
    """Récap secrets + estimation de tokens + écriture (fichier/stdout/presse-papier)."""
    if args.mask_secrets and _SECRET_HITS:
        kinds = ", ".join(sorted({k for _, k in _SECRET_HITS}))
        nfiles = len({f for f, _ in _SECRET_HITS})
        print(f"🔒 {len(_SECRET_HITS)} secret(s) masqué(s) dans {nfiles} fichier(s) "
              f"[{kinds}]", file=sys.stderr)
    tokens, method = estimate_tokens(output)

    if args.stdout:
        sys.stdout.write(output)
        return 0

    summary = (f"{head} — {len(output):,} caractères — "
               f"~{tokens:,} tokens ({method})").replace(",", " ")

    # Par défaut : fichier auto-nommé dans le répertoire courant. -o le surcharge.
    target = Path(args.output) if args.output else Path(output_name(root, args))
    target.write_text(output, encoding="utf-8")
    print(f"{summary}\n→ écrit dans {target}", file=sys.stderr)
    if args.clipboard and to_clipboard(output):
        print("→ copié aussi dans le presse-papier", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
