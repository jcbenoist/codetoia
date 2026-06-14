"""C++ : grammaire, signatures, et graphe d'appel.

Appelants qualifiés par classe englobante, puis namespace, puis fichier. Les
définitions hors-ligne `void A::f()` sont rattachées à la classe `A`.
`.h` est « possédé » par C (dispatch) mais ajouté au filtre --lang c++ via filter_extra.
"""
from __future__ import annotations

from .base import Lang, descend, txt


def _name_and_scope(node, data):
    """(scope | None, nom simple) depuis le déclarateur d'une function_definition."""
    fdecl = next(descend(node.child_by_field_name("declarator") or node,
                         {"function_declarator"}), None)
    if fdecl is None:
        return None, "?"
    d = fdecl.child_by_field_name("declarator")
    if d is None:
        return None, "?"
    if d.type == "qualified_identifier":          # A::f (définition hors-ligne)
        scope = d.child_by_field_name("scope")
        name = d.child_by_field_name("name")
        simple = txt(name, data) if name is not None else txt(d, data)
        return (txt(scope, data) if scope is not None else None), simple
    if d.type in ("identifier", "field_identifier", "destructor_name", "operator_name"):
        return None, txt(d, data)
    idn = next(descend(d, {"identifier", "field_identifier"}), None)
    return None, txt(idn, data) if idn is not None else "?"


def _callees(body, data):
    for call in descend(body, {"call_expression"}):
        fn = call.child_by_field_name("function")
        if fn is None:
            continue
        if fn.type == "identifier":               # f(...)
            yield txt(fn, data)
        elif fn.type == "field_expression":       # obj.m(...), p->m(...)
            f = fn.child_by_field_name("field")
            if f is not None:
                yield txt(f, data)
        elif fn.type == "qualified_identifier":   # N::f(...), C::m(...)
            n = fn.child_by_field_name("name")
            if n is not None:
                yield txt(n, data)


def extract_calls(tree, data, file, root):
    out = []

    def walk(node, cls, ns):
        if node.type == "namespace_definition":
            nm = node.child_by_field_name("name")
            ns = txt(nm, data) if nm is not None else ns
            for c in node.children:
                walk(c, cls, ns)
            return
        if node.type in ("class_specifier", "struct_specifier"):
            nm = node.child_by_field_name("name")
            cls = txt(nm, data) if nm is not None else cls
            for c in node.children:
                walk(c, cls, ns)
            return
        if node.type == "function_definition":
            scope, simple = _name_and_scope(node, data)
            qual = scope or cls or ns or file.stem
            body = node.child_by_field_name("body")
            callees = list(_callees(body, data)) if body is not None else []
            out.append((f"{qual}.{simple}", simple, callees))
            return
        for c in node.children:
            walk(c, cls, ns)

    walk(tree, None, None)
    return out


LANG = Lang(
    name="cpp", group="c++",
    extensions=(".cpp", ".cc", ".cxx", ".c++", ".hpp", ".hh", ".hxx"),
    filter_extra=(".h",), aliases=("cpp", "cxx", "cc"),
    grammar=("tree_sitter_cpp", "language"),
    body_containers=frozenset({"function_definition"}),
    extract_calls=extract_calls,
    line_comment="//", block_comment=("/*", "*/"),
)
