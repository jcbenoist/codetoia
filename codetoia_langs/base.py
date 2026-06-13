"""Socle commun aux modules de langage.

Contient le descripteur `Lang`, les helpers tree-sitter (stables entre versions de
l'API), le chargement/cache des parsers, et les marqueurs de commentaires des
langages *sans* module dédié (pour --strip-comments uniquement).
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Callable, Optional

# Une entrée de graphe d'appel : (label affiché, clé d'appariement | None, [clés appelées]).
# clé None = nœud appelant seulement (ex. test case Robot, non appelable).
Func = tuple[str, Optional[str], list[str]]

# Corps repliables en "{ ... }" pour le mode signatures, selon la grammaire.
BLOCK_BODY_TYPES = {"block", "statement_block", "compound_statement"}


@dataclass(frozen=True)
class Lang:
    """Tout ce qui concerne un langage : sélection, grammaire, signatures, callgraph."""
    name: str                                    # clé interne unique
    group: str                                   # abréviation --lang (go, cs, c, c++, js, ts, rf)
    extensions: tuple[str, ...]                  # extensions « possédées » (dispatch)
    filter_extra: tuple[str, ...] = ()           # extensions ajoutées au filtre --lang seulement
    aliases: tuple[str, ...] = ()                # noms tolérés pour --lang
    grammar: Optional[tuple[str, str]] = None    # (module pip, fonction grammaire tree-sitter)
    body_containers: frozenset = frozenset()     # signatures : nœuds dont le corps → { ... }
    collect_bodies: Optional[Callable] = None    # override signatures (Robot) ; sinon body_containers
    extract_calls: Optional[Callable] = None     # callgraph (tree, data, file, root) -> list[Func]
    line_comment: Optional[str] = None
    block_comment: Optional[tuple[str, str]] = None


# --- helpers tree-sitter (API stable : node.type / children / child_by_field_name) ---

def txt(node, data: bytes) -> str:
    return data[node.start_byte:node.end_byte].decode("utf-8", "replace")


def descend(node, types: set):
    """Itère récursivement les nœuds dont le type est dans `types`."""
    if node.type in types:
        yield node
    for child in node.children:
        yield from descend(child, types)


def field_name(node, data: bytes, fallback: set) -> str:
    n = node.child_by_field_name("name")
    if n is not None:
        return txt(n, data)
    for c in node.children:
        if c.type in fallback:
            return txt(c, data)
    return "?"


def body_node(node):
    body = node.child_by_field_name("body")
    if body is not None:
        return body
    for child in node.children:  # repli : bloc, ou flèche C#
        if child.type in BLOCK_BODY_TYPES or child.type == "arrow_expression_clause":
            return child
    return None


def collapse_blocks(node, containers: frozenset, edits: list) -> None:
    """Signatures (langages à accolades) : remplace le corps des `containers` par { ... }."""
    if node.type in containers:
        body = body_node(node)
        if body is not None and body.end_byte > body.start_byte:
            if body.type == "arrow_expression_clause":     # C# `=> expr`
                edits.append((body.start_byte, body.end_byte, b"=> ..."))
                return
            if body.type in BLOCK_BODY_TYPES:               # corps à accolades
                edits.append((body.start_byte, body.end_byte, b"{ ... }"))
                return
            # corps-expression (arrow JS court) : conservé ; on continue à descendre
    for child in node.children:
        collapse_blocks(child, containers, edits)


# --- chargement des parsers (robuste aux versions de tree-sitter 0.21 → 0.25) ---

_parsers: dict = {}
MISSING: set = set()   # noms de langages dont la grammaire n'a pas pu être chargée


def get_parser(lang: Lang):
    if lang.name not in _parsers:
        _parsers[lang.name] = _load_parser(lang)
        if _parsers[lang.name] is None:
            MISSING.add(lang.name)
    return _parsers[lang.name]


def _load_parser(lang: Lang):
    if lang.grammar is None:
        return None
    module_name, func_name = lang.grammar
    try:
        import tree_sitter
        module = importlib.import_module(module_name)
    except Exception:
        return None
    try:
        capsule = getattr(module, func_name)()
        try:
            language = tree_sitter.Language(capsule)            # tree_sitter >= 0.22
        except TypeError:
            language = tree_sitter.Language(capsule, lang.name)  # tree_sitter 0.21
        try:
            return tree_sitter.Parser(language)                # API récente
        except TypeError:
            parser = tree_sitter.Parser()                      # API ancienne
            try:
                parser.language = language
            except (AttributeError, TypeError):
                parser.set_language(language)
            return parser
    except Exception:
        return None


# --- commentaires des langages SANS module dédié (pour --strip-comments) ---
# Les langages tree-sitter portent leurs propres marqueurs dans leur module.
EXTRA_LINE_COMMENT = {
    ".py": "#", ".rb": "#", ".sh": "#", ".bash": "#", ".zsh": "#", ".yaml": "#",
    ".yml": "#", ".toml": "#", ".pl": "#", ".r": "#", ".jl": "#",
    ".java": "//", ".rs": "//", ".swift": "//", ".kt": "//", ".php": "//",
    ".scala": "//", ".dart": "//",
}
EXTRA_BLOCK_COMMENT = {
    ext: ("/*", "*/")
    for ext in (".java", ".rs", ".swift", ".kt", ".php", ".scala", ".dart",
                ".css", ".scss", ".less")
}
