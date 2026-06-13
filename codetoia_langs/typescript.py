"""TypeScript : deux grammaires distinctes (.ts/.mts/.cts vs .tsx), même groupe --lang.

tree-sitter-typescript expose language_typescript() et language_tsx() au lieu de
language(). Les conteneurs de corps sont ceux de JavaScript (factorisation).
"""
from __future__ import annotations

from .base import Lang
from .javascript import CONTAINERS

TS = Lang(
    name="ts", group="ts", extensions=(".ts", ".mts", ".cts"), aliases=("typescript",),
    grammar=("tree_sitter_typescript", "language_typescript"),
    body_containers=CONTAINERS,
    line_comment="//", block_comment=("/*", "*/"),
)
TSX = Lang(
    name="tsx", group="ts", extensions=(".tsx",),
    grammar=("tree_sitter_typescript", "language_tsx"),
    body_containers=CONTAINERS,
    line_comment="//", block_comment=("/*", "*/"),
)
