"""Registre des langages.

Agrège les descripteurs `Lang` de chaque module et expose au cœur (codetoia.py)
une API symétrique : sélection par extension/langage, extraction des signatures,
graphe d'appel, marqueurs de commentaires, paquets pip des grammaires.
"""
from __future__ import annotations

from . import base, c, cpp, csharp, go, javascript, robot, typescript
from .base import MISSING, Lang, collapse_blocks, get_parser  # noqa: F401 (ré-exports)

# Ordre = ordre d'affichage des abréviations --lang.
LANGS: list[Lang] = [
    go.LANG, csharp.LANG, c.LANG, cpp.LANG,
    javascript.LANG, typescript.TS, typescript.TSX, robot.LANG,
]

# Extension → langage (dispatch signatures/callgraph). Premier déclarant gagne.
BY_EXT: dict[str, Lang] = {}
for _l in LANGS:
    for _e in _l.extensions:
        BY_EXT.setdefault(_e, _l)

# Groupes --lang (abréviation → extensions) + alias, dans l'ordre des LANGS.
GROUP_EXTS: dict[str, list[str]] = {}
FILTER_NAMES: list[str] = []
ALIASES: dict[str, str] = {}
for _l in LANGS:
    if _l.group not in GROUP_EXTS:
        GROUP_EXTS[_l.group] = []
        FILTER_NAMES.append(_l.group)
    for _e in (*_l.extensions, *_l.filter_extra):
        if _e not in GROUP_EXTS[_l.group]:
            GROUP_EXTS[_l.group].append(_e)
    for _a in _l.aliases:
        ALIASES[_a] = _l.group

# Marqueurs de commentaires : langages secondaires + ceux portés par les modules.
_LINE = dict(base.EXTRA_LINE_COMMENT)
_BLOCK = dict(base.EXTRA_BLOCK_COMMENT)
for _l in LANGS:
    for _e in _l.extensions:
        if _l.line_comment:
            _LINE[_e] = _l.line_comment
        if _l.block_comment:
            _BLOCK[_e] = _l.block_comment

# Groupes de callgraph (ts+tsx fusionnés), dans l'ordre des LANGS.
CALLGRAPH_NAMES: list[str] = []
for _l in LANGS:
    if _l.extract_calls is not None and _l.callgraph_group not in CALLGRAPH_NAMES:
        CALLGRAPH_NAMES.append(_l.callgraph_group)


def comment_markers(ext: str):
    """(marqueur ligne | None, (ouvrant, fermant) | None) pour une extension."""
    return _LINE.get(ext), _BLOCK.get(ext)


def resolve_filter(spec: str):
    """'go,c#' → (extensions, [inconnus]) pour --lang."""
    exts: list[str] = []
    unknown: list[str] = []
    for raw in spec.split(","):
        n = raw.strip().lower()
        if not n:
            continue
        n = ALIASES.get(n, n)
        if n in GROUP_EXTS:
            exts += GROUP_EXTS[n]
        else:
            unknown.append(raw.strip())
    return exts, unknown


def signatures(source: str, ext: str):
    """Remplace les corps par { ... }. None si langage non supporté ou parse impossible."""
    lang = BY_EXT.get(ext)
    if lang is None or lang.grammar is None:
        return None
    parser = get_parser(lang)
    if parser is None:
        return None
    data = source.encode("utf-8")
    root = parser.parse(data).root_node
    edits: list[tuple[int, int, bytes]] = []
    if lang.collect_bodies is not None:
        lang.collect_bodies(root, data, edits)
    else:
        collapse_blocks(root, lang.body_containers, edits)
    if not edits:
        return source
    edits.sort()
    out, pos = bytearray(), 0
    for start, end, rep in edits:
        out += data[pos:start] + rep
        pos = end
    out += data[pos:]
    return out.decode("utf-8", "replace")


def build_callgraph(files, root):
    """Graphes d'appel par groupe de langage. Liste (groupe, texte) ou None.

    Les fichiers sont regroupés par `callgraph_group` (ts+tsx fusionnés) et chacun
    est parsé avec sa propre grammaire ; une section <call_graph> par groupe.
    """
    groups: dict[str, list] = {}
    for p in files:
        lang = BY_EXT.get(p.suffix.lower())
        if lang is None or lang.extract_calls is None:
            continue
        groups.setdefault(lang.callgraph_group, []).append((p, lang))
    sections = []
    for group in CALLGRAPH_NAMES:           # ordre stable (ordre des LANGS)
        if group in groups:
            graph = _graph_for(groups[group], root)
            if graph:
                sections.append((group, graph))
    return sections or None


def _graph_for(items, root):
    funcs = []
    name_to_labels: dict[str, set] = {}
    for p, lang in items:
        parser = get_parser(lang)
        if parser is None:
            continue
        try:
            data = p.read_text(encoding="utf-8", errors="replace").encode("utf-8")
        except OSError:
            continue
        tree = parser.parse(data).root_node
        for label, key, callees in lang.extract_calls(tree, data, p, root):
            funcs.append((label, key, callees))
            if key is not None:
                name_to_labels.setdefault(key, set()).add(label)

    declared = set(name_to_labels)
    forward = []
    reverse: dict[str, list] = {}
    for label, key, callees in funcs:
        resolved, seen = [], set()
        for cc in callees:
            if cc in declared and cc not in seen and cc != key:
                seen.add(cc)
                labels = name_to_labels[cc]
                resolved.append(next(iter(labels)) if len(labels) == 1 else cc)
        if resolved:
            forward.append((label, resolved))
            for r in resolved:
                callers = reverse.setdefault(r, [])
                if label not in callers:
                    callers.append(label)

    if not forward:
        return None
    lines = ["# appelant -> appelés"]
    lines += [f"{lbl} -> {', '.join(cs)}" for lbl, cs in forward]
    lines.append("# appelés <- appelants (analyse d'impact)")
    lines += [f"{c} <- {', '.join(reverse[c])}" for c in sorted(reverse)]
    return "\n".join(lines)


def grammar_packages() -> list[str]:
    """Paquets pip des grammaires tree-sitter du registre (pour --setup)."""
    mods = {lang.grammar[0] for lang in LANGS if lang.grammar}
    return sorted(m.replace("_", "-") for m in mods)
