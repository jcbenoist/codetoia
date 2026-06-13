"""Go : grammaire, signatures, graphe d'appel (qualifié par dossier ≈ package)."""
from __future__ import annotations

from .base import Lang, descend, txt


def _func_label(node, data):
    name = node.child_by_field_name("name")
    simple = txt(name, data) if name is not None else "?"
    recv = node.child_by_field_name("receiver")  # méthode : (e *Engine)
    if recv is not None:
        tid = next(descend(recv, {"type_identifier"}), None)
        if tid is not None:
            return f"{txt(tid, data)}.{simple}", simple
    return simple, simple


def _callees(body, data):
    for call in descend(body, {"call_expression"}):
        fn = call.child_by_field_name("function")
        if fn is None:
            continue
        if fn.type == "identifier":              # foo(...)
            yield txt(fn, data)
        elif fn.type == "selector_expression":   # e.Close(...), pkg.Query(...)
            f = fn.child_by_field_name("field")
            if f is not None:
                yield txt(f, data)


def extract_calls(tree, data, file, root):
    qual = file.parent.name or root.resolve().name
    out = []
    for node in descend(tree, {"function_declaration", "method_declaration"}):
        rest, simple = _func_label(node, data)
        body = node.child_by_field_name("body")
        callees = list(_callees(body, data)) if body is not None else []
        out.append((f"{qual}.{rest}", simple, callees))
    return out


LANG = Lang(
    name="go", group="go", extensions=(".go",), aliases=("golang",),
    grammar=("tree_sitter_go", "language"),
    body_containers=frozenset({"function_declaration", "method_declaration", "func_literal"}),
    extract_calls=extract_calls,
    line_comment="//", block_comment=("/*", "*/"),
)
