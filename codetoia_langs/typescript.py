"""TypeScript : deux grammaires distinctes (.ts/.mts/.cts vs .tsx), un seul graphe.

tree-sitter-typescript expose language_typescript() et language_tsx() au lieu de
language(). Conteneurs de corps et extraction d'appels = ceux de JavaScript
(factorisation). `cg_group="ts"` fusionne .ts et .tsx dans un même graphe d'appel.
"""
from __future__ import annotations

from .base import Lang
from .javascript import CONTAINERS, extract_calls

TS = Lang(
    name="ts", group="ts", cg_group="ts",
    extensions=(".ts", ".mts", ".cts"), aliases=("typescript",),
    grammar=("tree_sitter_typescript", "language_typescript"),
    body_containers=CONTAINERS, extract_calls=extract_calls,
    line_comment="//", block_comment=("/*", "*/"),
)
TSX = Lang(
    name="tsx", group="ts", cg_group="ts",
    extensions=(".tsx",),
    grammar=("tree_sitter_typescript", "language_tsx"),
    body_containers=CONTAINERS, extract_calls=extract_calls,
    line_comment="//", block_comment=("/*", "*/"),
)
