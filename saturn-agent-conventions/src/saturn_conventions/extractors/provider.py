from __future__ import annotations

from pathlib import Path

from tree_sitter import Node

from saturn_conventions.extractors._common import class_name
from saturn_conventions.parser import node_text, walk
from saturn_conventions.schemas.conventions import (
    NodeRegistration,
    ProviderConvention,
)


def extract_provider(class_def: Node, source: bytes, file_path: Path) -> ProviderConvention:
    name = class_name(class_def) or "<anonymous>"
    root_arg = _extract_rocket_builder_root_arg(class_def, source)
    chain = _extract_chain(class_def, source)
    finish_called = any(method == "finish" for method, _ in chain)
    nodes = _extract_node_registrations(chain)
    global_args = _extract_global_args(chain)

    return ProviderConvention(
        name=name,
        file_path=str(file_path),
        rocket_builder_root_arg=root_arg,
        global_args=global_args,
        nodes=nodes,
        finish_called=finish_called,
    )


def is_provider(class_def: Node, source: bytes) -> bool:
    """Heuristic: extends RocketBuilder(...), or class name ends with RocketProvider."""
    name = class_name(class_def) or ""
    if name.endswith("RocketProvider"):
        return True
    ec = next((c for c in class_def.children if c.type == "extends_clause"), None)
    if ec is None:
        return False
    for c in ec.children:
        if c.type == "type_identifier" and node_text(c, source).strip() == "RocketBuilder":
            return True
    return False


def _extract_rocket_builder_root_arg(class_def: Node, source: bytes) -> str | None:
    """For `extends RocketBuilder("foo_reports")`, returns 'foo_reports'."""
    ec = next((c for c in class_def.children if c.type == "extends_clause"), None)
    if ec is None:
        return None
    saw_rb = False
    for c in ec.children:
        if c.type == "type_identifier" and node_text(c, source).strip() == "RocketBuilder":
            saw_rb = True
            continue
        if saw_rb and c.type == "arguments":
            for a in c.named_children:
                if a.type == "string":
                    text = node_text(a, source)
                    return text[1:-1] if len(text) >= 2 and text.startswith('"') else text
    return None


def _unwind_chain(call_expr: Node) -> list[Node]:
    """Given an outer call_expression like a.b().c().d(), return the call_expressions
    in source order (a, b, c, d ...). Tree is left-recursive so we descend then reverse."""
    chain: list[Node] = []
    node: Node | None = call_expr
    while node is not None and node.type == "call_expression":
        chain.append(node)
        fn = next((c for c in node.children if c.type == "field_expression"), None)
        if fn is None:
            break
        inner = next((c for c in fn.children if c.type == "call_expression"), None)
        node = inner
    return list(reversed(chain))


def _method_and_args(call_expr: Node, source: bytes) -> tuple[str, list[str]]:
    """For a call_expression, return (method_name, [arg_text, ...])."""
    fn = next((c for c in call_expr.children if c.type == "field_expression"), None)
    if fn is not None:
        method_id = next(
            (c for c in fn.children if c.type == "identifier"), None
        )
        method = method_id.text.decode("utf-8") if method_id is not None else "?"
    else:
        method_id = next((c for c in call_expr.children if c.type == "identifier"), None)
        method = method_id.text.decode("utf-8") if method_id is not None else "?"

    args_node = next((c for c in call_expr.children if c.type == "arguments"), None)
    args: list[str] = []
    if args_node is not None:
        for a in args_node.named_children:
            args.append(node_text(a, source))
    return method, args


def _extract_chain(class_def: Node, source: bytes) -> list[tuple[str, list[str]]]:
    body = next((c for c in class_def.children if c.type == "template_body"), None)
    if body is None:
        return []

    flat: list[tuple[str, list[str]]] = []
    for stmt in body.named_children:
        # The chain is a single top-level call_expression (the outermost call)
        if stmt.type != "call_expression":
            continue
        for inner in _unwind_chain(stmt):
            flat.append(_method_and_args(inner, source))
    return flat


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in {'"', "'"}:
        return s[1:-1]
    return s


def _extract_node_registrations(
    chain: list[tuple[str, list[str]]]
) -> list[NodeRegistration]:
    """Pulls out .add(new <Class>(...), "<node_id>", List(...)) invocations.

    Subsequent .nodeArg(name, value) calls (until the next .add) attach to the
    previous registration.
    """
    registrations: list[NodeRegistration] = []
    for method, args in chain:
        if method == "add":
            node_class = _extract_new_class(args[0]) if args else "?"
            node_id = _strip_quotes(args[1]) if len(args) >= 2 else "?"
            children = _extract_string_list(args[2]) if len(args) >= 3 else []
            registrations.append(
                NodeRegistration(
                    node_class=node_class,
                    node_id=node_id,
                    children=children,
                )
            )
        elif method == "nodeArg" and registrations and len(args) >= 2:
            registrations[-1].node_args.append(
                (_strip_quotes(args[0]), _strip_quotes(args[1]))
            )
    return registrations


def _extract_global_args(chain: list[tuple[str, list[str]]]) -> list[tuple[str, str]]:
    """globalArg("name") → ("name", ""); globalArg("name", "value") → ("name", "value")."""
    out: list[tuple[str, str]] = []
    for method, args in chain:
        if method != "globalArg":
            continue
        if len(args) == 1:
            out.append((_strip_quotes(args[0]), ""))
        elif len(args) >= 2:
            out.append((_strip_quotes(args[0]), _strip_quotes(args[1])))
    return out


def _extract_new_class(arg_text: str) -> str:
    """From `new FooNode(_, _, _)`, return 'FooNode'."""
    s = arg_text.strip()
    if s.startswith("new "):
        s = s[4:]
    head = s.split("(", 1)[0].strip()
    return head or "?"


def _extract_string_list(arg_text: str) -> list[str]:
    """From `List("a", "b", "c")` or `Seq("a","b")`, return the strings.
    Falls back to splitting on commas if the literal form is unrecognized."""
    s = arg_text.strip()
    for prefix in ("List(", "Seq("):
        if s.startswith(prefix) and s.endswith(")"):
            inner = s[len(prefix):-1]
            return [
                _strip_quotes(part.strip())
                for part in _split_top_level(inner, ",")
                if part.strip()
            ]
    return []


def _split_top_level(text: str, sep: str) -> list[str]:
    depth = 0
    out: list[str] = []
    current: list[str] = []
    in_str = False
    for ch in text:
        if ch == '"':
            in_str = not in_str
        if not in_str:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif ch == sep and depth == 0:
                out.append("".join(current))
                current = []
                continue
        current.append(ch)
    out.append("".join(current))
    return out
