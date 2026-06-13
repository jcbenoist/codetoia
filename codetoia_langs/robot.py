"""Robot Framework : signatures (étapes → ...) et graphe d'appel (keywords).

Pas d'accolades : la logique des signatures et des appels est dédiée. Les keywords
s'apparient insensibles à la casse/espaces/underscores ; les built-ins (Log, etc.)
ne sont pas déclarés dans le projet et donc filtrés.
"""
from __future__ import annotations

from .base import Lang, descend, field_name, txt


def _norm(name: str) -> str:
    return name.lower().replace(" ", "").replace("_", "")


def _calls(body, data):
    for kw in descend(body, {"keyword"}):                 # invocation directe
        yield _norm(txt(kw, data))
    for va in descend(body, {"variable_assignment"}):     # ${x}=  Mon KW  args
        args = next((c for c in va.children if c.type == "arguments"), None)
        if args is not None:
            first = next(iter(args.named_children), None)  # 1er argument = le keyword
            if first is not None:
                yield _norm(txt(first, data))


def extract_calls(tree, data, file, root):
    out = []
    for node in descend(tree, {"keyword_definition", "test_case_definition"}):
        name = field_name(node, data, {"name"})
        body = next((c for c in node.children if c.type == "body"), None)
        callees = list(_calls(body, data)) if body is not None else []
        # un test case appelle des keywords mais n'est pas appelable (clé None)
        key = _norm(name) if node.type == "keyword_definition" else None
        out.append((f"{file.stem}.{name}", key, callees))
    return out


def collect_bodies(node, data, edits):
    """Signatures Robot : garde le nom + les [Settings], remplace les étapes par `...`."""
    if node.type in ("keyword_definition", "test_case_definition"):
        body = next((c for c in node.children if c.type == "body"), None)
        if body is not None:
            steps = [c for c in body.children if c.type == "statement"]
            if steps:
                edits.append((steps[0].start_byte, steps[-1].end_byte, b"..."))
        return
    for c in node.children:
        collect_bodies(c, data, edits)


LANG = Lang(
    name="rf", group="rf", extensions=(".robot", ".resource"),
    aliases=("robot", "robotframework"),
    grammar=("tree_sitter_robot", "language"),
    collect_bodies=collect_bodies,   # signatures : logique dédiée
    extract_calls=extract_calls,
)
