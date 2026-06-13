"""C++ : grammaire et signatures (callgraph pas encore implémenté).

`.h` est « possédé » par C (dispatch) mais ajouté au filtre --lang c++ via filter_extra.
"""
from __future__ import annotations

from .base import Lang

LANG = Lang(
    name="cpp", group="c++",
    extensions=(".cpp", ".cc", ".cxx", ".c++", ".hpp", ".hh", ".hxx"),
    filter_extra=(".h",), aliases=("cpp", "cxx", "cc"),
    grammar=("tree_sitter_cpp", "language"),
    body_containers=frozenset({"function_definition"}),
    line_comment="//", block_comment=("/*", "*/"),
)
