"""C : grammaire, signatures, et graphe d'appel (qualifié par fichier)."""
from __future__ import annotations

from .base import Lang, descend, txt


def _func_name(node, data):
    fdecl = next(descend(node.child_by_field_name("declarator") or node,
                         {"function_declarator"}), None)
    if fdecl is None:
        return "?"
    d = fdecl.child_by_field_name("declarator")
    if d is None:
        return "?"
    if d.type == "identifier":
        return txt(d, data)
    idn = next(descend(d, {"identifier"}), None)  # déballe pointer_declarator, etc.
    return txt(idn, data) if idn is not None else "?"


def _callees(body, data):
    for call in descend(body, {"call_expression"}):
        fn = call.child_by_field_name("function")
        if fn is None:
            continue
        if fn.type == "identifier":            # foo(...)
            yield txt(fn, data)
        elif fn.type == "field_expression":    # s.m(...), p->m(...)
            f = fn.child_by_field_name("field")
            if f is not None:
                yield txt(f, data)


def extract_calls(tree, data, file, root):
    out = []
    for node in descend(tree, {"function_definition"}):
        simple = _func_name(node, data)
        body = node.child_by_field_name("body")
        callees = list(_callees(body, data)) if body is not None else []
        out.append((f"{file.stem}.{simple}", simple, callees))
    return out


LANG = Lang(
    name="c", group="c", extensions=(".c", ".h"),
    grammar=("tree_sitter_c", "language"),
    body_containers=frozenset({"function_definition"}),
    extract_calls=extract_calls,
    line_comment="//", block_comment=("/*", "*/"),
)
