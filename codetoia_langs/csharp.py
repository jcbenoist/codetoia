"""C# : grammaire, signatures, graphe d'appel (qualifié par classe englobante)."""
from __future__ import annotations

from .base import Lang, body_node, descend, field_name, txt

_TYPES = {"class_declaration", "struct_declaration", "interface_declaration",
          "record_declaration"}
_FUNCS = {"method_declaration", "constructor_declaration", "local_function_statement"}


def _callees(body, data):
    for call in descend(body, {"invocation_expression"}):
        fn = call.child_by_field_name("function")
        if fn is None:
            continue
        if fn.type == "identifier":                  # Foo(...)
            yield txt(fn, data)
        elif fn.type == "member_access_expression":  # x.Foo(...), C.Foo(...)
            n = fn.child_by_field_name("name")
            if n is not None:
                yield txt(n, data)


def extract_calls(tree, data, file, root):
    out = []

    def walk(node, cls):
        if node.type in _TYPES:
            cls = field_name(node, data, {"identifier"})
            for c in node.children:
                walk(c, cls)
            return
        if node.type in _FUNCS:
            simple = field_name(node, data, {"identifier"})
            body = body_node(node)
            callees = list(_callees(body, data)) if body is not None else []
            out.append((f"{cls}.{simple}" if cls else simple, simple, callees))
            return
        for c in node.children:
            walk(c, cls)

    walk(tree, None)
    return out


LANG = Lang(
    name="cs", group="cs", extensions=(".cs",), aliases=("c#", "csharp"),
    grammar=("tree_sitter_c_sharp", "language"),
    body_containers=frozenset({
        "method_declaration", "constructor_declaration", "destructor_declaration",
        "operator_declaration", "conversion_operator_declaration",
        "local_function_statement", "accessor_declaration",
    }),
    extract_calls=extract_calls,
    line_comment="//", block_comment=("/*", "*/"),
)
