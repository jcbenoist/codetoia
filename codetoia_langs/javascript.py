"""JavaScript : grammaire et signatures (callgraph pas encore implémenté).

`CONTAINERS` est réutilisé par TypeScript/TSX (factorisation).
"""
from __future__ import annotations

from .base import Lang

# Nœuds-fonctions JS/TS dont le corps (statement_block) est replié en { ... }.
CONTAINERS = frozenset({
    "function_declaration", "function_expression", "arrow_function",
    "method_definition", "generator_function", "generator_function_declaration",
})

LANG = Lang(
    name="js", group="js", extensions=(".js", ".jsx", ".mjs", ".cjs"),
    aliases=("javascript", "node"),
    grammar=("tree_sitter_javascript", "language"),
    body_containers=CONTAINERS,
    line_comment="//", block_comment=("/*", "*/"),
)
