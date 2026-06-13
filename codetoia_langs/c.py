"""C : grammaire et signatures (callgraph pas encore implémenté)."""
from __future__ import annotations

from .base import Lang

LANG = Lang(
    name="c", group="c", extensions=(".c", ".h"),
    grammar=("tree_sitter_c", "language"),
    body_containers=frozenset({"function_definition"}),
    line_comment="//", block_comment=("/*", "*/"),
)
