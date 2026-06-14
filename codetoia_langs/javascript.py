"""JavaScript : grammaire, signatures, graphe d'appel.

`CONTAINERS` et `extract_calls` sont réutilisés par TypeScript/TSX (factorisation) —
les grammaires diffèrent mais les types de nœuds sont identiques.
"""
from __future__ import annotations

from .base import Lang, descend, txt

# Nœuds-fonctions JS/TS dont le corps (statement_block) est replié en { ... }.
CONTAINERS = frozenset({
    "function_declaration", "function_expression", "arrow_function",
    "method_definition", "generator_function", "generator_function_declaration",
})


def _callees(body, data):
    for call in descend(body, {"call_expression"}):
        fn = call.child_by_field_name("function")
        if fn is None:
            continue
        if fn.type == "identifier":          # foo(...)
            yield txt(fn, data)
        elif fn.type == "member_expression":  # obj.m(...), this.m(...)
            pr = fn.child_by_field_name("property")
            if pr is not None:
                yield txt(pr, data)


def extract_calls(tree, data, file, root):
    """Graphe d'appel JS/TS/TSX : méthodes qualifiées par classe, sinon par fichier."""
    out = []

    def emit(label, simple, body):
        out.append((label, simple, list(_callees(body, data)) if body is not None else []))

    def walk(node, cls):
        t = node.type
        if t == "class_declaration":
            nm = node.child_by_field_name("name")
            cls = txt(nm, data) if nm is not None else cls
            for c in node.children:
                walk(c, cls)
            return
        if t == "function_declaration":
            nm = node.child_by_field_name("name")
            simple = txt(nm, data) if nm is not None else "?"
            emit(f"{file.stem}.{simple}", simple, node.child_by_field_name("body"))
            return
        if t == "method_definition":
            nm = node.child_by_field_name("name")
            simple = txt(nm, data) if nm is not None else "?"
            label = f"{cls}.{simple}" if cls else f"{file.stem}.{simple}"
            emit(label, simple, node.child_by_field_name("body"))
            return
        if t == "variable_declarator":  # const f = () => {...} / function expr
            val = node.child_by_field_name("value")
            if val is not None and val.type in ("arrow_function", "function_expression",
                                                "function", "generator_function"):
                nm = node.child_by_field_name("name")
                simple = txt(nm, data) if nm is not None else "?"
                emit(f"{file.stem}.{simple}", simple, val.child_by_field_name("body"))
                return
        for c in node.children:
            walk(c, cls)

    walk(tree, None)
    return out


LANG = Lang(
    name="js", group="js", extensions=(".js", ".jsx", ".mjs", ".cjs"),
    aliases=("javascript", "node"),
    grammar=("tree_sitter_javascript", "language"),
    body_containers=CONTAINERS,
    extract_calls=extract_calls,
    line_comment="//", block_comment=("/*", "*/"),
)
